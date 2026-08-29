# Signal for Omarchy

A Signal client for the [Omarchy](https://omarchy.org/) bar. The window lists conversations already on this computer, shows photos and videos in the thread, and sends replies after you link Omarchy as a Signal device.

![Plugin preview with sample conversations](preview.png)

Plugins run unsandboxed inside `omarchy-shell`. Read this repository before enabling it.

## Requirements

- Omarchy Quattro
- [Signal Desktop](https://signal.org/download/) signed in on this computer (history is read from its local database)
- Python 3 with a user venv (`sqlcipher3-binary`, `cryptography`) created automatically under `~/.local/state/omarchy/signal/venv`
- `qrencode` and `zenity` for the device-link QR code and file picker
- A one-time download of [signal-cli](https://github.com/AsamK/signal-cli) **v0.14.7** Linux-native (~110 MB), verified against a pinned SHA-256 before it is extracted

The plugin never writes Signal Desktop's database and never prints its key. It does not use `sudo`. Sending uses signal-cli as an extra linked device, not as a replacement for Signal Desktop.

## Install

```sh
omarchy plugin add https://github.com/Krustington/omarchy-signal.git --enable
```

Then place the widget if it is not already on the bar:

```sh
omarchy bar move krusty.signal --section right
```

## Use

| Input | Action |
| --- | --- |
| Left click the bar icon | Open or close the Signal window |
| Right click | Open official Signal Desktop |
| Middle click | Refresh |
| Enter in the composer | Send (Shift+Enter for a new line) |
| `+` | Attach a photo or file |

First send: click **Link device**, scan the QR from Signal on your phone (Settings → Linked devices). History still comes from Signal Desktop on this computer; the link is only for sending.

## Update

```sh
omarchy plugin update krusty.signal
```

## Remove

```sh
omarchy plugin remove krusty.signal
rm -rf ~/.local/state/omarchy/signal
```

That removes the plugin, its venv, downloaded signal-cli, and decrypted media cache. Signal Desktop and your messages stay put. Unlink **Omarchy** from your phone if you added it.

## License

MIT. See [LICENSE](LICENSE).

External dependencies: Signal Desktop (your existing install), [signal-cli](https://github.com/AsamK/signal-cli) (LGPL-3.0, downloaded on first link), Python packages `sqlcipher3-binary` and `cryptography` in a plugin-owned venv.

## Trademark

Signal is a trademark of Signal Messenger, LLC. This project is not affiliated with, endorsed by, or sponsored by Signal.
