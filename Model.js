// Pure helpers for the Signal bar widget. No Qt, no IO — node can test this.

function defaultStatus() {
  return {
    ok: true,
    installed: false,
    running: false,
    focused: false,
    themeName: "",
    themeMode: "dark",
    colorScheme: "",
    themeMatched: true,
    unreadCount: 0,
    conversations: [],
    lastError: ""
  }
}

function parseStatus(raw) {
  var text = String(raw || "").trim()
  if (text === "") return defaultStatus()
  try {
    var parsed = JSON.parse(text)
    if (!parsed || typeof parsed !== "object") return defaultStatus()
    var failed = defaultStatus()
    if (parsed.ok === false) {
      failed.ok = false
      failed.lastError = String(parsed.lastError || "Failed to read Signal status")
      return failed
    }
    parsed.conversations = Array.isArray(parsed.conversations) ? parsed.conversations : []
    parsed.unreadCount = Math.max(0, Number(parsed.unreadCount || 0) || 0)
    parsed.installed = parsed.installed === true
    parsed.running = parsed.running === true
    parsed.focused = parsed.focused === true
    parsed.themeMatched = parsed.themeMatched !== false
    parsed.themeName = String(parsed.themeName || "")
    parsed.themeMode = String(parsed.themeMode || "dark")
    parsed.colorScheme = String(parsed.colorScheme || "")
    parsed.lastError = String(parsed.lastError || "")
    parsed.ok = true
    return parsed
  } catch (e) {
    var broken = defaultStatus()
    broken.ok = false
    broken.lastError = "Failed to parse Signal status"
    return broken
  }
}

function isSignalNotification(entry) {
  if (!entry || typeof entry !== "object") return false
  var app = String(entry.app || "").toLowerCase()
  var icon = String(entry.appIcon || "").toLowerCase()
  return app === "signal" || icon === "signal-desktop" || icon === "signal"
}

function conversationName(entry) {
  var name = String(entry && entry.summary || "").trim()
  return name === "" ? "Signal" : name
}

function groupConversations(notifications, lastSeenTs, limit) {
  var seenTs = Number(lastSeenTs || 0)
  if (!isFinite(seenTs)) seenTs = 0
  var cap = Number(limit || 12)
  if (!isFinite(cap) || cap < 1) cap = 12

  var list = Array.isArray(notifications) ? notifications : []
  var groups = {}
  var order = []

  for (var i = 0; i < list.length; i++) {
    var item = list[i]
    if (!isSignalNotification(item)) continue
    var name = conversationName(item)
    var ts = Number(item.timestamp || 0)
    if (!isFinite(ts)) ts = 0
    var unread = ts > seenTs
    var group = groups[name]
    if (!group) {
      group = {
        name: name,
        preview: String(item.body || ""),
        timestamp: ts,
        unread: unread ? 1 : 0,
        count: 1
      }
      groups[name] = group
      order.push(name)
    } else {
      group.count += 1
      if (unread) group.unread += 1
      if (ts >= group.timestamp) {
        group.timestamp = ts
        group.preview = String(item.body || group.preview)
      }
    }
  }

  order.sort(function(a, b) {
    return (groups[b].timestamp || 0) - (groups[a].timestamp || 0)
  })

  var out = []
  for (var j = 0; j < order.length && out.length < cap; j++) out.push(groups[order[j]])
  return out
}

function unreadTotal(conversations) {
  var list = Array.isArray(conversations) ? conversations : []
  var total = 0
  for (var i = 0; i < list.length; i++) total += Math.max(0, Number(list[i].unread || 0) || 0)
  return total
}

function firstLetter(word) {
  var text = String(word || "")
  for (var i = 0; i < text.length; i++) {
    var c = text.charAt(i)
    if ((c >= "0" && c <= "9") || c.toLowerCase() !== c.toUpperCase()) return c
  }
  return ""
}

function initials(name) {
  var text = String(name || "").trim()
  if (text === "") return "?"
  var parts = text.split(/\s+/)
  var letters = []
  for (var i = 0; i < parts.length && letters.length < 2; i++) {
    var ch = firstLetter(parts[i])
    if (ch) letters.push(ch)
  }
  if (letters.length === 0) return "?"
  return letters.join("").toUpperCase()
}

function relativeTime(timestampMs, nowMs) {
  var ts = Number(timestampMs || 0)
  if (!isFinite(ts) || ts <= 0) return "Unknown time"
  var now = nowMs === undefined ? Date.now() : Number(nowMs)
  var diff = Math.max(0, Math.floor((now - ts) / 1000))
  if (diff < 60) return "Just now"
  var minutes = Math.floor(diff / 60)
  if (minutes < 60) return minutes + "m ago"
  var hours = Math.floor(minutes / 60)
  if (hours < 24) return hours + "h ago"
  var days = Math.floor(hours / 24)
  if (days < 30) return days + "d ago"
  var months = Math.floor(days / 30)
  if (months < 12) return months + "mo ago"
  return Math.floor(days / 365) + "y ago"
}

function barTooltip(status) {
  if (!status || !status.installed) return "Signal is not installed"
  var unread = Number(status.unreadCount || 0) || 0
  if (unread > 0) return unread === 1 ? "1 unread Signal message" : unread + " unread Signal messages"
  if (status.running) return "Signal is running — click to open the panel"
  return "Open Signal"
}

function heroMeta(status) {
  if (!status || !status.installed) return "Not installed"
  var unread = Number(status.unreadCount || 0) || 0
  if (unread > 0) return unread === 1 ? "1 unread" : unread + " unread"
  if (status.focused) return "On this workspace"
  if (status.running) return "Running"
  return "Closed"
}

function heroDetail(status) {
  if (status && status.running) return "ON"
  if (status && status.installed) return ""
  return "OFF"
}

function themeLine(status) {
  var name = String(status && status.themeName || "").trim()
  var mode = String(status && status.themeMode || "dark").trim()
  if (name === "") name = "Omarchy"
  return "Following " + name + " · " + mode
}

function openOnClickIsApp(value) {
  return String(value || "Panel").toLowerCase() === "app"
}

function markSeenOnOpen(value) {
  var text = String(value || "On").toLowerCase()
  return text !== "off" && text !== "false" && text !== "0"
}

function formatBytes(bytes) {
  var value = Number(bytes || 0)
  if (!isFinite(value) || value <= 0) return ""
  var units = ["B", "KB", "MB", "GB"]
  var index = 0
  while (value >= 1000 && index < units.length - 1) {
    value = value / 1000
    index++
  }
  var decimals = index === 0 || value >= 10 ? 0 : 1
  return value.toFixed(decimals).replace(/\.0$/, "") + " " + units[index]
}

function mediaList(message) {
  if (!message) return []
  if (typeof message.mediaJson === "string" && message.mediaJson !== "") {
    try {
      var parsed = JSON.parse(message.mediaJson)
      if (Array.isArray(parsed)) return parsed
    } catch (e) {}
  }
  if (Array.isArray(message.media)) return message.media
  return []
}

function domainFromUrl(url) {
  var text = String(url || "")
  var match = text.match(/^https?:\/\/([^\/]+)/i)
  return match ? match[1].replace(/^www\./, "") : text
}

if (typeof module !== "undefined") {
  module.exports = {
    defaultStatus: defaultStatus,
    parseStatus: parseStatus,
    isSignalNotification: isSignalNotification,
    conversationName: conversationName,
    groupConversations: groupConversations,
    unreadTotal: unreadTotal,
    initials: initials,
    relativeTime: relativeTime,
    barTooltip: barTooltip,
    heroMeta: heroMeta,
    heroDetail: heroDetail,
    themeLine: themeLine,
    openOnClickIsApp: openOnClickIsApp,
    markSeenOnOpen: markSeenOnOpen
  }
}
