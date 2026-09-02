#!/usr/bin/env python3
"""Signal engine for the Omarchy plugin.

Reads the official Signal Desktop SQLCipher database (history, contacts,
unread). Sends and receives live traffic through a linked signal-cli device.
Never prints the database key. Never writes Signal Desktop's files.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import select
import shutil
import signal as posix
import socket
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

HOME = Path.home()
STATE = Path(os.environ.get("XDG_STATE_HOME", HOME / ".local" / "state")) / "omarchy" / "signal"
VENV_DIR = STATE / "venv"
VENV_PY = VENV_DIR / "bin" / "python"
CLI_DIR = STATE / "signal-cli"
CLI_BIN = CLI_DIR / "bin" / "signal-cli"
CLI_VERSION = "0.14.7"
CLI_URL = (
    f"https://github.com/AsamK/signal-cli/releases/download/v{CLI_VERSION}/"
    f"signal-cli-{CLI_VERSION}-Linux-native.tar.gz"
)
CLI_SHA256 = "0fe065294adcf35df4c249b635d0ce57de7765d4fec660bffaa2e7f0549d4e5f"
CLI_ELF_SHA256 = "7dc5933215b6bedafab7ff0983466c0d0ae98bc20538f5186e7d1d600ccde63f"
PY_PACKAGES = ("sqlcipher3-binary==0.6.0", "cryptography==50.0.1")
QR_PATH = STATE / "link.png"
SEEN_PATH = STATE / "seen.json"
SETTINGS_PATH = STATE / "settings.json"
MEDIA_DIR = STATE / "media"
DESKTOP_DIR = HOME / ".config" / "Signal"
DB_PATH = DESKTOP_DIR / "sql" / "db.sqlite"
CONFIG_PATH = DESKTOP_DIR / "config.json"
ATTACH_ROOT = DESKTOP_DIR / "attachments.noindex"
STDOUT_LIMIT = 2 * 1024 * 1024
MAX_CIPHERTEXT = 32 * 1024 * 1024
MAX_PLAINTEXT = 24 * 1024 * 1024
MAX_RPC = 256 * 1024
MAX_DOWNLOAD = 180 * 1024 * 1024
DOWNLOAD_SECONDS = 180
OPEN_NOFOLLOW = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC

MIME_EXT = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/heic": "heic",
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/aac": "aac",
    "application/pdf": "pdf",
}


def file_url(path: Path) -> str:
    return "file://" + str(path)


def _trusted_bin(name: str) -> Path:
    raw = Path("/usr/bin") / name
    resolved = raw.resolve()
    if not str(resolved).startswith("/usr/") or not resolved.is_file():
        raise RuntimeError(f"missing trusted binary {name}")
    if not os.access(resolved, os.X_OK):
        raise RuntimeError(f"not executable: {resolved}")
    return resolved


def _private_dir(path: Path) -> Path:
    if path.exists() and path.is_symlink():
        raise RuntimeError(f"{path} must not be a symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    st = path.lstat()
    if stat.S_ISLNK(st.st_mode) or st.st_uid != os.getuid() or (st.st_mode & 0o777) != 0o700:
        raise RuntimeError(f"{path} is not a private directory")
    return path


def _runtime_dir() -> Path:
    xdg = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    root = Path(xdg) / "omarchy-signal" if xdg else STATE / "run"
    return _private_dir(root)


def sock_path() -> Path:
    return _runtime_dir() / "signal-cli.sock"


def ident_path() -> Path:
    return _runtime_dir() / "daemon.ident"


def ensure_state_dirs() -> None:
    _private_dir(STATE)
    _private_dir(MEDIA_DIR)
    _private_dir(CLI_DIR)
    _private_dir(CLI_BIN.parent)


def ensure_venv() -> Path:
    ensure_state_dirs()
    marker = VENV_DIR / ".deps"
    want = "\n".join(PY_PACKAGES) + "\n"
    if VENV_PY.is_file() and marker.is_file() and marker.read_text(encoding="utf-8") == want:
        return VENV_PY
    py = _trusted_bin("python3")
    subprocess.run([str(py), "-m", "venv", str(VENV_DIR)], check=True, timeout=90)
    subprocess.run(
        [str(VENV_PY), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", *PY_PACKAGES],
        check=True,
        timeout=180,
    )
    marker.write_text(want, encoding="utf-8")
    os.chmod(marker, 0o600)
    return VENV_PY


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _open_confined(root: Path, rel: str | None) -> int | None:
    if not rel or not isinstance(rel, str):
        return None
    if rel.startswith("/") or "\\" in rel or "\x00" in rel:
        return None
    parts = [p for p in rel.split("/") if p]
    if not parts or any(p in (".", "..") for p in parts):
        return None
    opened: list[int] = []
    try:
        fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        opened.append(fd)
        for i, part in enumerate(parts):
            flags = OPEN_NOFOLLOW
            if i < len(parts) - 1:
                flags |= os.O_DIRECTORY
            fd = os.open(part, flags, dir_fd=opened[-1])
            opened.append(fd)
        leaf = opened.pop()
        st = os.fstat(leaf)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_size > MAX_CIPHERTEXT:
            os.close(leaf)
            return None
        return leaf
    except OSError:
        return None
    finally:
        for fd in opened:
            try:
                os.close(fd)
            except OSError:
                pass


def _read_capped(fd: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total < limit:
        buf = os.read(fd, min(1024 * 1024, limit - total))
        if not buf:
            break
        chunks.append(buf)
        total += len(buf)
    extra = os.read(fd, 1)
    if extra:
        raise ValueError("file exceeds cap")
    return b"".join(chunks)


def _looks_like_media(data: bytes) -> bool:
    if len(data) < 12:
        return False
    if data[:3] == b"\xff\xd8\xff":
        return True
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    if data[4:8] == b"ftyp":
        return True
    if data[:4] == b"%PDF":
        return True
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3"):
        return True
    return False


def mime_kind(mime: str, attachment_type: str) -> str:
    if attachment_type == "preview":
        return "preview"
    if attachment_type == "sticker":
        return "sticker"
    mime = (mime or "").lower()
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    return "file"


def decrypt_attachment(raw: bytes, local_key: str | None, size: int | None, version: int) -> bytes:
    if int(version or 0) < 2 or not local_key:
        return raw[: min(int(size or len(raw)), MAX_PLAINTEXT, len(raw))]
    keys = base64.b64decode(local_key)
    if len(keys) != 64:
        raise ValueError("invalid localKey")
    cipher_key, mac_key = keys[:32], keys[32:]
    if len(raw) < 48:
        raise ValueError("attachment too short")
    iv, mac, data = raw[:16], raw[-32:], raw[16:-32]
    if len(data) % 16 != 0:
        raise ValueError("invalid ciphertext length")
    if not hmac.compare_digest(hmac.new(mac_key, iv + data, hashlib.sha256).digest(), mac):
        raise ValueError("MAC mismatch")
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    decryptor = Cipher(algorithms.AES(cipher_key), modes.CBC(iv)).decryptor()
    plain = decryptor.update(data) + decryptor.finalize()
    limit = min(int(size or len(plain)), MAX_PLAINTEXT, len(plain))
    return plain[:limit]


def materialize_file(rel: str | None, local_key: str | None, size: int | None, version: int, mime: str, hint: str) -> str:
    if not rel:
        return ""
    ext = MIME_EXT.get((mime or "").lower(), Path(hint or "").suffix.lstrip(".") or "bin")
    if ext not in MIME_EXT.values() and ext not in ("bin", "jpg", "jpeg"):
        ext = "bin"
    digest = hashlib.sha256(f"{rel}|{size}|{local_key or ''}".encode()).hexdigest()[:32]
    dest = MEDIA_DIR / f"{digest}.{ext}"
    if dest.name != f"{digest}.{ext}":
        return ""
    try:
        if dest.is_file() and not dest.is_symlink() and 0 < dest.stat().st_size <= MAX_PLAINTEXT:
            return file_url(dest)
    except OSError:
        pass
    fd = _open_confined(ATTACH_ROOT, str(rel))
    if fd is None:
        return ""
    try:
        raw = _read_capped(fd, MAX_CIPHERTEXT)
        data = decrypt_attachment(raw, local_key, size, int(version or 0))
    except Exception:
        return ""
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    if not data or not _looks_like_media(data):
        return ""
    ensure_state_dirs()
    return _finish_media(dest, data)


def _finish_media(dest: Path, data: bytes) -> str:
    media_fd = os.open(str(MEDIA_DIR), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    tmp_name = dest.name + ".tmp"
    try:
        try:
            os.unlink(tmp_name, dir_fd=media_fd)
        except OSError:
            pass
        tmp_fd = os.open(
            tmp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=media_fd,
        )
        try:
            os.write(tmp_fd, data)
            os.fsync(tmp_fd)
        finally:
            os.close(tmp_fd)
        os.replace(tmp_name, dest.name, src_dir_fd=media_fd, dst_dir_fd=media_fd)
    finally:
        os.close(media_fd)
    return file_url(dest)


def media_from_row(row: dict, preview_meta: dict | None = None) -> dict | None:
    rel = row.get("path") or row.get("thumbnailPath") or row.get("screenshotPath")
    if not rel:
        return None
    mime = str(row.get("contentType") or "")
    kind = mime_kind(mime, str(row.get("attachmentType") or "attachment"))
    name = str(row.get("fileName") or "")
    thumb_mime = str(row.get("thumbnailContentType") or row.get("screenshotContentType") or "image/jpeg")
    thumb = materialize_file(
        row.get("thumbnailPath") or row.get("screenshotPath"),
        row.get("thumbnailLocalKey") or row.get("screenshotLocalKey") or row.get("localKey"),
        row.get("thumbnailSize") or row.get("screenshotSize"),
        row.get("thumbnailVersion") or row.get("screenshotVersion") or row.get("version") or 0,
        thumb_mime,
        "thumb.jpg",
    )
    # Videos in the feed only need a still. Decrypt the full file when it is
    # small enough to keep open-on-click snappy; large clips stay a poster.
    size = int(row.get("size") or 0)
    if kind == "video" and size > 4_000_000:
        url = ""
    else:
        url = materialize_file(row.get("path"), row.get("localKey"), row.get("size"), row.get("version") or 0, mime, name)
    if not url and not thumb:
        return None
    item = {
        "kind": kind,
        "mime": mime,
        "name": name,
        "url": url or thumb,
        "thumb": thumb or url,
        "width": int(row.get("width") or 0),
        "height": int(row.get("height") or 0),
        "size": int(row.get("size") or 0),
        "duration": float(row.get("duration") or 0),
        "title": "",
        "description": "",
        "pageUrl": "",
    }
    if preview_meta:
        item["kind"] = "preview"
        item["title"] = str(preview_meta.get("title") or "")
        item["description"] = str(preview_meta.get("description") or "")
        item["pageUrl"] = str(preview_meta.get("url") or "")
    return item


def load_media_for_messages(db: DesktopDB, messages: list[dict]) -> None:
    ids = [m["id"] for m in messages if m.get("id")]
    if not ids:
        return
    placeholders = ",".join("?" * len(ids))
    rows = db.query(
        f"""
        SELECT messageId, attachmentType, contentType, path, thumbnailPath, screenshotPath,
               thumbnailLocalKey, screenshotLocalKey, thumbnailSize, screenshotSize,
               thumbnailContentType, screenshotContentType, thumbnailVersion, screenshotVersion,
               fileName, width, height, size, localKey, version, duration, orderInMessage
        FROM message_attachments
        WHERE messageId IN ({placeholders})
          AND IFNULL(pending, 0) = 0
        ORDER BY messageId, orderInMessage
        """,
        ids,
    )
    by_id: dict[str, list[dict]] = {}
    for row in rows:
        by_id.setdefault(str(row.get("messageId") or ""), []).append(row)
    for msg in messages:
        preview_list = msg.pop("_previews", None) or []
        media = []
        for row in by_id.get(msg["id"], []):
            atype = str(row.get("attachmentType") or "attachment")
            if atype in ("quote", "long-message", "contact"):
                continue
            meta = preview_list[0] if atype == "preview" and preview_list else None
            item = media_from_row(row, meta)
            if item:
                media.append(item)
        if not media and preview_list:
            p0 = preview_list[0]
            media.append({
                "kind": "preview",
                "mime": "",
                "name": "",
                "url": "",
                "thumb": "",
                "width": 0,
                "height": 0,
                "size": 0,
                "duration": 0,
                "title": str(p0.get("title") or ""),
                "description": str(p0.get("description") or ""),
                "pageUrl": str(p0.get("url") or ""),
            })
        msg["media"] = media
        msg["mediaJson"] = json.dumps(media, ensure_ascii=False)
        msg["attachmentCount"] = len([m for m in media if m["kind"] != "preview"])


_EMIT_LOCK = threading.Lock()


def emit(payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with _EMIT_LOCK:
        sys.stdout.write(text[:STDOUT_LIMIT] + "\n")
        sys.stdout.flush()


def fail(req_id, message: str) -> None:
    emit({"id": req_id, "ok": False, "error": str(message)})


def load_key() -> str:
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    key = str(data.get("key") or "")
    if len(key) < 32:
        raise RuntimeError("Signal Desktop is not set up on this machine")
    return key


def import_sqlcipher():
    from sqlcipher3 import dbapi2 as sqlite  # type: ignore

    return sqlite


class DesktopDB:
    def __init__(self) -> None:
        self.sqlite = import_sqlcipher()
        self.con = None
        self.lock = threading.Lock()
        self._tmpdir = None
        self.mtime = 0.0
        self.open()

    def wal_mtime(self) -> float:
        times = []
        for name in ("db.sqlite", "db.sqlite-wal"):
            path = DB_PATH.parent / name
            try:
                times.append(path.stat().st_mtime)
            except OSError:
                pass
        return max(times) if times else 0.0

    def open(self) -> None:
        if not DB_PATH.exists():
            raise RuntimeError("Signal Desktop database not found")
        key = load_key()
        last_error = None
        for factory in (self._open_live, self._open_snapshot):
            try:
                con = factory()
                cur = con.cursor()
                cur.execute(f"PRAGMA key = \"x'{key}'\"")
                cur.execute("SELECT count(*) FROM sqlite_master")
                self.close()
                self.con = con
                self.mtime = self.wal_mtime()
                return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                try:
                    con.close()
                except Exception:
                    pass
        raise RuntimeError(f"Could not open Signal Desktop database: {last_error}")

    def _open_live(self):
        uri = f"file:{DB_PATH}?mode=ro"
        return self.sqlite.connect(uri, uri=True, timeout=2)

    def _open_snapshot(self):
        if self._tmpdir:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
        self._tmpdir = tempfile.mkdtemp(prefix="omarchy-signal-db-")
        dest = Path(self._tmpdir)
        shutil.copy2(DB_PATH, dest / "db.sqlite")
        for extra in ("db.sqlite-wal", "db.sqlite-shm"):
            src = DB_PATH.parent / extra
            if src.exists():
                shutil.copy2(src, dest / extra)
        return self.sqlite.connect(str(dest / "db.sqlite"), timeout=2)

    def close(self) -> None:
        if self.con is not None:
            try:
                self.con.close()
            except Exception:
                pass
            self.con = None
        if self._tmpdir:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self._tmpdir = None

    def refresh_if_changed(self) -> bool:
        current = self.wal_mtime()
        if current <= self.mtime:
            return False
        with self.lock:
            self.open()
        return True

    def query(self, sql: str, params=()):
        with self.lock:
            if self.con is None:
                self.open()
            try:
                cur = self.con.cursor()
                cur.execute(sql, params)
                cols = [d[0] for d in cur.description] if cur.description else []
                return [dict(zip(cols, row)) for row in cur.fetchall()]
            except Exception:
                self.open()
                cur = self.con.cursor()
                cur.execute(sql, params)
                cols = [d[0] for d in cur.description] if cur.description else []
                return [dict(zip(cols, row)) for row in cur.fetchall()]


def parse_json(raw) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def display_name(row: dict, blob: dict) -> str:
    for value in (
        row.get("name"),
        blob.get("name"),
        row.get("profileFullName"),
        blob.get("profileFullName"),
        row.get("profileName"),
        blob.get("profileName"),
        row.get("e164"),
        blob.get("e164"),
    ):
        text = str(value or "").strip()
        if text:
            return text
    return "Unknown"


def load_seen() -> dict:
    try:
        data = json.loads(SEEN_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_seen(data: dict) -> None:
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SEEN_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data) + "\n", encoding="utf-8")
    tmp.replace(SEEN_PATH)


def load_settings() -> dict:
    defaults = {"keepDesktopBackground": True, "backgroundHintDismissed": False}
    try:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaults
    if not isinstance(raw, dict):
        return defaults
    if "keepDesktopBackground" in raw:
        defaults["keepDesktopBackground"] = raw["keepDesktopBackground"] is True
    if "backgroundHintDismissed" in raw:
        defaults["backgroundHintDismissed"] = raw["backgroundHintDismissed"] is True
    return defaults


def save_settings(data: dict) -> dict:
    current = load_settings()
    if "keepDesktopBackground" in data:
        current["keepDesktopBackground"] = data["keepDesktopBackground"] is True
    if "backgroundHintDismissed" in data:
        current["backgroundHintDismissed"] = data["backgroundHintDismissed"] is True
    ensure_state_dirs()
    tmp = SETTINGS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(current) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(SETTINGS_PATH)
    return current


def which_desktop() -> str | None:
    for path in (Path("/usr/bin/signal-desktop"), Path("/usr/lib/signal-desktop/signal-desktop")):
        try:
            if not path.is_file() or not os.access(path, os.X_OK):
                continue
            resolved = path.resolve()
        except OSError:
            continue
        if str(resolved).startswith("/usr/"):
            return str(resolved)
    return None


def desktop_running() -> bool:
    for pid_dir in Path("/proc").iterdir():
        if not pid_dir.name.isdigit():
            continue
        try:
            raw = (pid_dir / "cmdline").read_bytes().split(b"\x00")
        except OSError:
            continue
        argv0 = raw[0].decode("utf-8", "replace") if raw else ""
        if "signal-desktop" not in argv0:
            continue
        if b"--type=" in b" ".join(raw):
            continue
        return True
    return False


def _hide_desktop_window() -> None:
    hypr = Path("/usr/bin/hyprctl")
    if not hypr.is_file():
        return
    for match in ("class:^(signal)$", "class:^(Signal)$"):
        try:
            subprocess.run(
                [str(hypr), "dispatch", "movetoworkspacesilent", f"special:signal,{match}"],
                timeout=2,
                capture_output=True,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


def ensure_desktop() -> dict:
    installed = which_desktop()
    running = desktop_running()
    if running:
        return {"ok": True, "running": True, "started": False, "installed": bool(installed)}
    if not installed:
        return {"ok": False, "running": False, "started": False, "installed": False, "error": "Signal Desktop is not installed"}
    subprocess.Popen(
        [installed, "--start-in-tray", "--use-tray-icon"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(20):
        if desktop_running():
            _hide_desktop_window()
            return {"ok": True, "running": True, "started": True, "installed": True}
        time.sleep(0.25)
    return {"ok": False, "running": False, "started": False, "installed": True, "error": "Signal Desktop did not start"}


def show_desktop() -> dict:
    hypr = Path("/usr/bin/hyprctl")
    if desktop_running() and hypr.is_file():
        for match in ("class:^(signal)$", "class:^(Signal)$"):
            try:
                subprocess.run(
                    [str(hypr), "dispatch", "movetoworkspace", f"e+0,{match}"],
                    timeout=2,
                    capture_output=True,
                    check=False,
                )
                subprocess.run(
                    [str(hypr), "dispatch", "focuswindow", match],
                    timeout=2,
                    capture_output=True,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        return {"ok": True, "running": True}
    installed = which_desktop()
    if not installed:
        return {"ok": False, "error": "Signal Desktop is not installed"}
    subprocess.Popen(
        [installed],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {"ok": True, "started": True}


def avatar_url(blob: dict) -> str:
    av = blob.get("profileAvatar") if isinstance(blob.get("profileAvatar"), dict) else None
    if not av:
        av = blob.get("avatar") if isinstance(blob.get("avatar"), dict) else None
    if not av:
        return ""
    mime = str(av.get("contentType") or "image/jpeg")
    return materialize_file(
        av.get("path"),
        av.get("localKey"),
        av.get("size") or av.get("length"),
        av.get("version") or 0,
        mime,
        "avatar.jpg",
    )


def conversation_record(row: dict, seen: dict) -> dict:
    blob = parse_json(row.get("json"))
    cid = str(row.get("id") or "")
    last_ts = int(blob.get("timestamp") or row.get("active_at") or 0)
    desktop_unread = int(blob.get("unreadCount") or 0)
    local_seen = int(seen.get(cid) or 0)
    unread = 0 if local_seen >= last_ts and last_ts > 0 else desktop_unread
    kind = "group" if str(row.get("type") or blob.get("type") or "") == "group" else "private"
    return {
        "id": cid,
        "kind": kind,
        "name": display_name(row, blob),
        "preview": str(blob.get("lastMessage") or ""),
        "timestamp": last_ts,
        "unread": unread,
        "archived": blob.get("isArchived") is True,
        "pinned": blob.get("isPinned") is True,
        "e164": str(row.get("e164") or blob.get("e164") or ""),
        "serviceId": str(row.get("serviceId") or blob.get("serviceId") or ""),
        "groupId": str(row.get("groupId") or blob.get("groupId") or ""),
        "messageCount": int(blob.get("messageCount") or 0),
        "avatar": avatar_url(blob),
    }


def list_conversations(db: DesktopDB, query: str = "", limit: int = 80) -> list[dict]:
    seen = load_seen()
    rows = db.query(
        """
        SELECT id, type, name, profileName, profileFullName, e164, serviceId,
               groupId, active_at, json
        FROM conversations
        WHERE active_at IS NOT NULL
        ORDER BY active_at DESC
        LIMIT 500
        """
    )
    needle = query.strip().lower()
    out = []
    for row in rows:
        rec = conversation_record(row, seen)
        if needle and needle not in rec["name"].lower() and needle not in rec["preview"].lower():
            continue
        if rec["messageCount"] <= 0 and not rec["preview"] and not rec["unread"]:
            continue
        out.append(rec)
        if len(out) >= max(1, limit):
            break
    return out


def message_record(row: dict) -> dict:
    blob = parse_json(row.get("json"))
    body = str(row.get("body") or blob.get("body") or "")
    msg_type = str(row.get("type") or "")
    outgoing = msg_type == "outgoing"
    quote = blob.get("quote") if isinstance(blob.get("quote"), dict) else None
    previews = blob.get("preview") if isinstance(blob.get("preview"), list) else []
    return {
        "id": str(row.get("id") or ""),
        "conversationId": str(row.get("conversationId") or ""),
        "type": msg_type,
        "outgoing": outgoing,
        "body": body,
        "timestamp": int(row.get("sent_at") or row.get("timestamp") or 0),
        "hasAttachments": int(row.get("hasAttachments") or 0) == 1,
        "attachmentCount": 0,
        "quote": (quote.get("text") if quote else "") or "",
        "source": str(row.get("source") or blob.get("source") or ""),
        "media": [],
        "_previews": previews,
    }


def list_messages(db: DesktopDB, conversation_id: str, limit: int = 80, before: int | None = None) -> list[dict]:
    params: list = [conversation_id]
    clause = ""
    if before:
        clause = "AND sent_at < ?"
        params.append(int(before))
    params.append(max(1, min(int(limit), 200)))
    rows = db.query(
        f"""
        SELECT id, conversationId, type, body, sent_at, timestamp, hasAttachments, json, source
        FROM messages
        WHERE conversationId = ?
          AND type IN ('incoming', 'outgoing')
          AND IFNULL(isErased, 0) = 0
          {clause}
        ORDER BY sent_at DESC
        LIMIT ?
        """,
        params,
    )
    rows.reverse()
    messages = [message_record(row) for row in rows]
    load_media_for_messages(db, messages)
    return messages


def search_messages(db: DesktopDB, query: str, limit: int = 40) -> list[dict]:
    q = query.strip()
    if not q:
        return []
    try:
        rows = db.query(
            """
            SELECT m.id, m.conversationId, m.type, m.body, m.sent_at, m.timestamp,
                   m.hasAttachments, m.json, m.source
            FROM messages_fts
            JOIN messages m ON m.rowid = messages_fts.rowid
            WHERE messages_fts MATCH ?
            ORDER BY m.sent_at DESC
            LIMIT ?
            """,
            (q, max(1, min(int(limit), 80))),
        )
    except Exception:
        rows = db.query(
            """
            SELECT id, conversationId, type, body, sent_at, timestamp, hasAttachments, json, source
            FROM messages
            WHERE body LIKE ?
              AND type IN ('incoming', 'outgoing')
            ORDER BY sent_at DESC
            LIMIT ?
            """,
            (f"%{q}%", max(1, min(int(limit), 80))),
        )
    messages = [message_record(row) for row in rows]
    load_media_for_messages(db, messages)
    return messages


def unread_total(conversations: list[dict]) -> int:
    return sum(int(item.get("unread") or 0) for item in conversations)


def _cli_ok(path: Path) -> bool:
    try:
        st = path.lstat()
    except OSError:
        return False
    if path.name != "signal-cli" or stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return False
    if not os.access(path, os.X_OK) or st.st_uid != os.getuid():
        return False
    try:
        return _sha256_file(path) == CLI_ELF_SHA256
    except OSError:
        return False


def which_cli() -> str | None:
    if _cli_ok(CLI_BIN):
        return str(CLI_BIN)
    return None


def linked_accounts() -> list[str]:
    data = HOME / ".local" / "share" / "signal-cli" / "data"
    accounts_file = data / "accounts.json"
    found: list[str] = []
    if accounts_file.is_file():
        try:
            payload = json.loads(accounts_file.read_text(encoding="utf-8"))
            for item in payload.get("accounts") or []:
                if not isinstance(item, dict):
                    continue
                number = str(item.get("number") or "").strip()
                uuid = str(item.get("uuid") or "").strip()
                path = str(item.get("path") or "").strip()
                if number:
                    found.append(number)
                elif uuid:
                    found.append(uuid)
                elif path:
                    found.append(path)
        except (OSError, json.JSONDecodeError):
            pass
    if found:
        return sorted(set(found))
    if not data.is_dir():
        return []
    for path in data.iterdir():
        name = path.name
        if name in ("accounts.json",) or name.endswith(".d"):
            continue
        if name.startswith("+") or (len(name) == 36 and name.count("-") == 4) or name.isdigit():
            found.append(name)
    return sorted(set(found))


def _host_allowed(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return host == "github.com" or host.endswith(".github.com") or host.endswith(".githubusercontent.com")


class _BoundRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.count += 1
        if self.count > 4:
            raise RuntimeError("too many redirects")
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme != "https" or not _host_allowed(parsed.hostname or ""):
            raise RuntimeError("redirect origin not allowed")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download_https(url: str, dest: Path, max_bytes: int, timeout: int) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not _host_allowed(parsed.hostname or ""):
        raise RuntimeError("download origin not allowed")
    opener = urllib.request.build_opener(_BoundRedirect(), urllib.request.HTTPSHandler())
    req = urllib.request.Request(url, headers={"User-Agent": "krusty.signal"})
    deadline = time.time() + timeout
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with opener.open(req, timeout=30) as resp:
            final = urllib.parse.urlparse(resp.geturl())
            if final.scheme != "https" or not _host_allowed(final.hostname or ""):
                raise RuntimeError("final download origin not allowed")
            written = 0
            with tmp.open("wb") as out:
                while True:
                    if time.time() > deadline:
                        raise RuntimeError("download timed out")
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise RuntimeError("download too large")
                    out.write(chunk)
        tmp.replace(dest)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _find_extracted_cli(root: Path) -> Path | None:
    for path in root.rglob("*"):
        try:
            st = path.lstat()
        except OSError:
            continue
        if path.name == "signal-cli" and stat.S_ISREG(st.st_mode) and not stat.S_ISLNK(st.st_mode):
            return path
    return None


def ensure_cli() -> dict:
    existing = which_cli()
    if existing:
        return {"installed": True, "path": existing, "downloaded": False}
    ensure_state_dirs()
    archive = STATE / f"signal-cli-{CLI_VERSION}-Linux-native.tar.gz"
    if archive.exists() and _sha256_file(archive) != CLI_SHA256:
        archive.unlink()
    if not archive.exists():
        _download_https(CLI_URL, archive, MAX_DOWNLOAD, DOWNLOAD_SECONDS)
    if _sha256_file(archive) != CLI_SHA256:
        try:
            archive.unlink()
        except OSError:
            pass
        raise RuntimeError("signal-cli download did not match the expected checksum")
    staging = Path(tempfile.mkdtemp(prefix="omarchy-signal-cli-", dir=str(STATE)))
    try:
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(staging, filter="data")
        found = _find_extracted_cli(staging)
        if found is None or _sha256_file(found) != CLI_ELF_SHA256:
            raise RuntimeError("signal-cli binary missing or checksum mismatch after extract")
        if CLI_BIN.exists():
            CLI_BIN.unlink()
        os.rename(found, CLI_BIN)
        os.chmod(CLI_BIN, 0o700)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    if not which_cli():
        raise RuntimeError("signal-cli binary missing after download")
    return {"installed": True, "path": str(CLI_BIN), "downloaded": True}


def _proc_starttime(pid: int) -> str:
    raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    rest = raw[raw.rfind(")") + 2 :].split()
    return rest[19]


def _proc_exe(pid: int) -> str:
    return os.readlink(f"/proc/{pid}/exe")


def _save_ident(proc: subprocess.Popen) -> None:
    ident = {"pid": proc.pid, "starttime": _proc_starttime(proc.pid), "exe": _proc_exe(proc.pid)}
    path = ident_path()
    path.write_text(json.dumps(ident), encoding="utf-8")
    os.chmod(path, 0o600)


def _load_ident() -> dict | None:
    try:
        data = json.loads(ident_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _ident_matches(ident: dict) -> bool:
    try:
        pid = int(ident["pid"])
        if _proc_starttime(pid) != str(ident.get("starttime")):
            return False
        exe = _proc_exe(pid)
        return exe == str(ident.get("exe")) == str(CLI_BIN.resolve()) and _cli_ok(CLI_BIN)
    except (OSError, KeyError, ValueError):
        return False


def _stop_pg(proc: subprocess.Popen | None) -> None:
    if not proc or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, posix.SIGTERM)
    except OSError:
        try:
            proc.terminate()
        except OSError:
            pass
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, posix.SIGKILL)
        except OSError:
            try:
                proc.kill()
            except OSError:
                pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


def _read_line_deadline(fd: int, deadline: float, cap: int = 4096) -> str:
    buf = b""
    while time.time() < deadline and len(buf) < cap:
        remain = max(0.05, min(1.0, deadline - time.time()))
        ready, _, _ = select.select([fd], [], [], remain)
        if not ready:
            continue
        chunk = os.read(fd, 256)
        if not chunk:
            break
        buf += chunk
        if b"\n" in buf:
            return buf.split(b"\n", 1)[0].decode("utf-8", "replace").strip()
    return ""


def _read_capped_fd(fd: int, cap: int, timeout: float) -> str:
    buf = b""
    deadline = time.time() + timeout
    while time.time() < deadline and len(buf) < cap:
        ready, _, _ = select.select([fd], [], [], max(0.05, deadline - time.time()))
        if not ready:
            break
        chunk = os.read(fd, min(512, cap - len(buf)))
        if not chunk:
            break
        buf += chunk
    return buf.decode("utf-8", "replace")


def _recv_capped(sock: socket.socket, cap: int) -> bytes:
    buf = b""
    while len(buf) < cap:
        chunk = sock.recv(min(4096, cap - len(buf)))
        if not chunk:
            break
        buf += chunk
        if b"\n" in buf:
            break
    if len(buf) >= cap and b"\n" not in buf:
        raise RuntimeError("response too large")
    return buf


def _peer_uid(sock: socket.socket) -> int:
    creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", creds)
    return uid


def kill_our_daemon() -> None:
    ident = _load_ident()
    if not ident or not _ident_matches(ident):
        return
    pid = int(ident["pid"])
    try:
        os.killpg(pid, posix.SIGTERM)
    except OSError:
        try:
            os.kill(pid, posix.SIGTERM)
        except OSError:
            return
    deadline = time.time() + 3
    while time.time() < deadline and Path(f"/proc/{pid}").exists():
        time.sleep(0.1)
    if Path(f"/proc/{pid}").exists():
        try:
            os.killpg(pid, posix.SIGKILL)
        except OSError:
            try:
                os.kill(pid, posix.SIGKILL)
            except OSError:
                pass
    try:
        ident_path().unlink()
    except OSError:
        pass


class Linker:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.uri = ""
        self.error = ""
        self.done = False

    def start(self, device_name: str = "Omarchy") -> dict:
        self.stop()
        cli = which_cli() or ensure_cli()["path"]
        if not _cli_ok(Path(cli)):
            raise RuntimeError("signal-cli identity check failed")
        self.proc = subprocess.Popen(
            [cli, "link", "-n", device_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        assert self.proc.stdout is not None
        deadline = time.time() + 15
        uri = _read_line_deadline(self.proc.stdout.fileno(), deadline)
        if not (uri.startswith("sgnl://") or uri.startswith("https://signal.org")):
            err = ""
            if self.proc.stderr:
                err = _read_capped_fd(self.proc.stderr.fileno(), 8192, 1.0)
            self.error = err.strip() or "signal-cli did not return a link URI"
            self.stop()
            return {"ok": False, "error": self.error}
        self.uri = uri
        ensure_state_dirs()
        qr = _trusted_bin("qrencode")
        subprocess.run(
            [str(qr), "-o", str(QR_PATH), "-s", "8", "-m", "2", uri],
            check=True,
            capture_output=True,
            timeout=5,
        )
        threading.Thread(target=self._wait, daemon=True).start()
        return {"ok": True, "uri": uri, "qr": str(QR_PATH)}

    def _wait(self) -> None:
        if not self.proc:
            return
        try:
            code = self.proc.wait(timeout=300)
        except subprocess.TimeoutExpired:
            self.stop()
            self.error = "link timed out"
            self.done = True
            return
        self.done = True
        if code != 0 and not linked_accounts():
            err = ""
            if self.proc and self.proc.stderr:
                err = _read_capped_fd(self.proc.stderr.fileno(), 8192, 0.5)
            self.error = err.strip() or f"link exited {code}"

    def status(self) -> dict:
        accounts = linked_accounts()
        return {
            "running": bool(self.proc and self.proc.poll() is None),
            "done": self.done or bool(accounts),
            "linked": bool(accounts),
            "accounts": accounts,
            "uri": self.uri,
            "qr": str(QR_PATH) if QR_PATH.exists() else "",
            "error": self.error,
        }

    def stop(self) -> None:
        _stop_pg(self.proc)
        self.proc = None


def socket_alive() -> bool:
    path = sock_path()
    if not path.exists() or path.is_symlink():
        return False
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(1.5)
        sock.connect(str(path))
        if _peer_uid(sock) != os.getuid():
            return False
        sock.sendall(b'{"jsonrpc":"2.0","method":"version","id":"ping"}\n')
        buf = _recv_capped(sock, MAX_RPC)
        return b"jsonrpc" in buf or b"result" in buf
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


class CliDaemon:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.account = ""
        self._lock = threading.Lock()

    def start(self) -> dict:
        accounts = linked_accounts()
        if not accounts:
            return {"ok": False, "error": "No linked Signal account"}
        self.account = accounts[0]
        sock = sock_path()
        if socket_alive():
            ident = _load_ident()
            if ident and _ident_matches(ident):
                return {"ok": True, "account": self.account, "socket": str(sock), "reused": True}
        with self._lock:
            if socket_alive():
                ident = _load_ident()
                if ident and _ident_matches(ident):
                    return {"ok": True, "account": self.account, "socket": str(sock), "reused": True}
            cli = which_cli()
            if not cli:
                return {"ok": False, "error": "signal-cli is not installed"}
            kill_our_daemon()
            if sock.exists():
                try:
                    sock.unlink()
                except OSError:
                    pass
            log = _runtime_dir() / "daemon.log"
            log_f = open(log, "ab", buffering=0)
            self.proc = subprocess.Popen(
                [cli, "-a", self.account, "daemon", f"--socket={sock}", "--no-receive-stdout"],
                stdout=log_f,
                stderr=log_f,
                start_new_session=True,
            )
            _save_ident(self.proc)
            deadline = time.time() + 30
            while time.time() < deadline:
                if socket_alive():
                    return {"ok": True, "account": self.account, "socket": str(sock)}
                if self.proc.poll() is not None:
                    err = ""
                    try:
                        err = log.read_text(encoding="utf-8", errors="replace")[-400:]
                    except OSError:
                        err = f"exited {self.proc.returncode}"
                    return {"ok": False, "error": err.strip() or "signal-cli daemon exited"}
                time.sleep(0.25)
            return {"ok": False, "error": "signal-cli daemon did not create a socket"}

    def alive(self) -> bool:
        ident = _load_ident()
        return socket_alive() and bool(ident and _ident_matches(ident))

    def rpc(self, method: str, params: dict | None = None) -> dict:
        if not self.alive():
            started = self.start()
            if not started.get("ok"):
                raise RuntimeError(started.get("error") or "daemon unavailable")
        payload = {"jsonrpc": "2.0", "method": method, "id": str(time.time()), "params": params or {}}
        path = sock_path()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(20)
            sock.connect(str(path))
            if _peer_uid(sock) != os.getuid():
                raise RuntimeError("socket peer uid mismatch")
            sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
            raw = _recv_capped(sock, MAX_RPC).decode("utf-8", "replace").strip()
        finally:
            sock.close()
        if not raw:
            raise RuntimeError("empty response from signal-cli")
        parsed = None
        for line in raw.splitlines():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and ("result" in obj or "error" in obj):
                parsed = obj
        if parsed is None:
            raise RuntimeError(raw[:300])
        if parsed.get("error"):
            err = parsed["error"]
            raise RuntimeError(err.get("message") if isinstance(err, dict) else str(err))
        return parsed.get("result")


def _owned_regular_file(path: str) -> str | None:
    if not path.startswith("/") or ".." in path.split("/"):
        return None
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return None
    if st.st_uid != os.getuid() or st.st_size > MAX_CIPHERTEXT:
        return None
    return path


def send_message(db: DesktopDB, daemon: CliDaemon, conversation_id: str, text: str, attachments=None) -> dict:
    body = str(text or "").strip()
    files = []
    for item in attachments or []:
        path = str(item or "").replace("file://", "")
        owned = _owned_regular_file(path)
        if owned:
            files.append(owned)
    if not body and not files:
        raise RuntimeError("Message is empty")
    rows = db.query(
        "SELECT id, type, e164, serviceId, groupId, json FROM conversations WHERE id = ?",
        (conversation_id,),
    )
    if not rows:
        raise RuntimeError("Conversation not found")
    rec = conversation_record(rows[0], load_seen())
    params: dict
    if rec["kind"] == "group":
        if not rec["groupId"]:
            raise RuntimeError("This group has no Signal group id")
        params = {"groupId": rec["groupId"], "message": body}
    else:
        recipient = rec["serviceId"] or rec["e164"]
        if not recipient:
            raise RuntimeError("This conversation has no Signal address")
        params = {"recipient": [recipient], "message": body}
    if files:
        params["attachments"] = files
    result = daemon.rpc("send", params)
    preview = body if body else (files[0].rsplit("/", 1)[-1] if files else "")
    return {"timestamp": (result or {}).get("timestamp") if isinstance(result, dict) else result, "preview": preview}


def mark_read(conversation_id: str, timestamp: int) -> None:
    seen = load_seen()
    current = int(seen.get(conversation_id) or 0)
    if timestamp > current:
        seen[conversation_id] = int(timestamp)
        save_seen(seen)


class Engine:
    def __init__(self) -> None:
        self.db = DesktopDB()
        self.linker = Linker()
        self.daemon = CliDaemon()

    def status(self) -> dict:
        convos = list_conversations(self.db, "", 200)
        accounts = linked_accounts()
        settings = load_settings()
        return {
            "ok": True,
            "desktop": DB_PATH.exists(),
            "desktopInstalled": bool(which_desktop()),
            "desktopRunning": desktop_running(),
            "cliInstalled": bool(which_cli()),
            "linked": bool(accounts),
            "accounts": accounts,
            "daemon": self.daemon.alive(),
            "unreadCount": unread_total(convos),
            "conversationCount": len(convos),
            "canSend": bool(accounts),
            "keepDesktopBackground": settings["keepDesktopBackground"],
            "backgroundHintDismissed": settings["backgroundHintDismissed"],
            "themeName": theme_name(),
        }

    def handle(self, req: dict) -> None:
        req_id = req.get("id")
        cmd = str(req.get("cmd") or "")
        try:
            if cmd == "status":
                emit({"id": req_id, "ok": True, "result": self.status()})
            elif cmd == "conversations":
                items = list_conversations(self.db, str(req.get("q") or ""), int(req.get("limit") or 80))
                emit({"id": req_id, "ok": True, "result": {"conversations": items, "unreadCount": unread_total(items)}})
            elif cmd == "messages":
                items = list_messages(
                    self.db,
                    str(req.get("conversationId") or ""),
                    int(req.get("limit") or 80),
                    req.get("before"),
                )
                emit({"id": req_id, "ok": True, "result": {"messages": items}})
            elif cmd == "search":
                items = search_messages(self.db, str(req.get("q") or ""), int(req.get("limit") or 40))
                emit({"id": req_id, "ok": True, "result": {"messages": items}})
            elif cmd == "send":
                result = send_message(
                    self.db,
                    self.daemon,
                    str(req.get("conversationId") or ""),
                    str(req.get("text") or ""),
                    req.get("attachments") or [],
                )
                emit({"id": req_id, "ok": True, "result": result})
            elif cmd == "markRead":
                mark_read(str(req.get("conversationId") or ""), int(req.get("timestamp") or 0))
                emit({"id": req_id, "ok": True, "result": {"ok": True}})
            elif cmd == "ensureCli":
                emit({"id": req_id, "ok": True, "result": ensure_cli()})
            elif cmd == "startLink":
                emit({"id": req_id, "ok": True, "result": self.linker.start(str(req.get("deviceName") or "Omarchy"))})
            elif cmd == "linkStatus":
                emit({"id": req_id, "ok": True, "result": self.linker.status()})
            elif cmd == "startDaemon":
                def _run(rid=req_id):
                    try:
                        emit({"id": rid, "ok": True, "result": self.daemon.start()})
                    except Exception as exc:  # noqa: BLE001
                        fail(rid, str(exc))

                threading.Thread(target=_run, daemon=True).start()
            elif cmd == "getSettings":
                emit({"id": req_id, "ok": True, "result": load_settings()})
            elif cmd == "setSettings":
                st = save_settings(req)
                if st.get("keepDesktopBackground"):
                    ensure_desktop()
                emit({"id": req_id, "ok": True, "result": {**st, "desktopRunning": desktop_running()}})
            elif cmd == "ensureDesktop":
                emit({"id": req_id, "ok": True, "result": ensure_desktop()})
            elif cmd == "showDesktop":
                emit({"id": req_id, "ok": True, "result": show_desktop()})
            else:
                fail(req_id, f"Unknown command: {cmd}")
        except Exception as exc:  # noqa: BLE001
            fail(req_id, str(exc))


def theme_name() -> str:
    path = HOME / ".local" / "state" / "omarchy" / "current" / "theme.name"
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return " ".join(part.capitalize() for part in raw.replace("_", "-").split("-"))


def serve() -> None:
    engine = Engine()
    stop = threading.Event()

    def watch():
        while not stop.is_set():
            try:
                if engine.db.refresh_if_changed():
                    emit({"event": "changed"})
            except Exception:
                pass
            stop.wait(2.5)

    threading.Thread(target=watch, daemon=True).start()
    emit({"event": "ready", "result": engine.status()})
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                fail(None, "invalid json")
                continue
            if not isinstance(req, dict):
                fail(None, "invalid request")
                continue
            engine.handle(req)
    finally:
        stop.set()
        engine.db.close()


def main() -> None:
    ensure_venv()
    here = str(Path(__file__).resolve())
    if Path(sys.executable).resolve() != VENV_PY.resolve():
        os.execv(str(VENV_PY), [str(VENV_PY), here, *sys.argv[1:]])
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "serve":
        serve()
        return
    engine = Engine()
    req = {"id": 1, "cmd": cmd}
    if cmd == "conversations" and len(sys.argv) > 2:
        req["q"] = sys.argv[2]
    engine.handle(req)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        emit({"ok": False, "error": str(exc)})
        sys.exit(1)
