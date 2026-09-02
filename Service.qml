import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons

// Long-lived Signal host. Talks to engine.py over stdin/stdout JSON lines.
Item {
  id: root
  visible: false
  width: 0
  height: 0

  property var shell: null
  property var manifest: null
  property var settings: ({})

  readonly property string pluginId: manifest && manifest.id ? String(manifest.id) : "krusty.signal"
  readonly property string pluginDir: manifest && manifest.__sourceDir ? String(manifest.__sourceDir) : Qt.resolvedUrl(".").toString().replace(/^file:\/\//, "")
  readonly property string systemPython: "/usr/bin/python3"
  readonly property string enginePath: Qt.resolvedUrl("engine.py").toString().replace(/^file:\/\//, "")

  property bool windowOpen: false
  property bool ready: false
  property bool desktop: false
  property bool cliInstalled: false
  property bool linked: false
  property bool canSend: false
  property bool daemon: false
  property int unreadCount: 0
  property int conversationCount: 0
  property var conversations: []
  property var messages: []
  property var searchHits: []
  property string selectedId: ""
  property string query: ""
  property string lastError: ""
  property string actionStatus: ""
  property string linkUri: ""
  property string linkQr: ""
  property bool linking: false
  property bool daemonStarting: false
  property var accounts: []
  property int nextId: 1
  property var pending: ({})

  readonly property var selected: {
    for (var i = 0; i < conversations.length; i++)
      if (conversations[i].id === selectedId) return conversations[i]
    return null
  }
  readonly property string barTooltip: unreadCount > 0
    ? (unreadCount === 1 ? "1 unread Signal message" : unreadCount + " unread Signal messages")
    : "Signal"

  function sendCmd(cmd, extra, callback) {
    var id = nextId++
    var req = extra ? extra : ({})
    req.id = id
    req.cmd = cmd
    if (callback) {
      var bag = pending
      bag[id] = callback
      pending = bag
    }
    if (!engine.running) engine.running = true
    engine.write(JSON.stringify(req) + "\n")
  }

  function handleLine(line) {
    var raw = String(line || "").trim()
    if (raw === "") return
    var obj
    try { obj = JSON.parse(raw) } catch (e) { return }
    if (obj.event === "ready" && obj.result) {
      lastError = ""
      applyStatus(obj.result)
      ready = true
      refreshConversations()
      return
    }
    if (obj.event === "changed") {
      refreshConversations()
      if (selectedId !== "") loadMessages(selectedId)
      return
    }
    var cb = pending[obj.id]
    if (cb) {
      var bag = pending
      delete bag[obj.id]
      pending = bag
      cb(obj)
    }
    if (obj.ok === false && obj.error) {
      var err = String(obj.error)
      if (err.indexOf("socket") === -1 && err.indexOf("Config file is in use") === -1)
        lastError = err
    }
  }

  function applyStatus(st) {
    if (!st) return
    desktop = st.desktop === true
    cliInstalled = st.cliInstalled === true
    var hasAccount = st.linked === true || (st.accounts && st.accounts.length > 0)
    if (hasAccount) {
      linked = true
      canSend = true
      linking = false
      if (linkPoll.running) linkPoll.stop()
    } else if (st.linked === false && Array.isArray(st.accounts) && st.accounts.length === 0) {
      linked = false
    }
    daemon = st.daemon === true
    unreadCount = Number(st.unreadCount || 0)
    conversationCount = Number(st.conversationCount || 0)
    accounts = st.accounts || []
    if (linked && !daemon && !daemonStarting) startDaemon()
  }

  function refresh() {
    sendCmd("status", {}, function(obj) {
      if (obj.ok && obj.result) applyStatus(obj.result)
    })
    refreshConversations()
  }

  function refreshConversations() {
    sendCmd("conversations", { q: query, limit: 200 }, function(obj) {
      if (!obj.ok || !obj.result) return
      conversations = obj.result.conversations || []
      unreadCount = Number(obj.result.unreadCount || 0)
    })
  }

  function search(text) {
    query = String(text || "")
    refreshConversations()
  }

  function openConversation(id) {
    selectedId = String(id || "")
    if (selectedId === "") {
      messages = []
      return
    }
    loadMessages(selectedId)
    var conv = selected
    var ts = conv && conv.timestamp ? conv.timestamp : Date.now()
    sendCmd("markRead", { conversationId: selectedId, timestamp: ts }, function() { refreshConversations() })
  }

  function loadMessages(id) {
    sendCmd("messages", { conversationId: id, limit: 100 }, function(obj) {
      if (obj.ok && obj.result) messages = obj.result.messages || []
    })
  }

  function sendMessage(text, attachments) {
    if (!canSend || selectedId === "") return
    var body = String(text || "").trim()
    var files = attachments || []
    if (body === "" && files.length === 0) return
    actionStatus = "Sending…"
    sendCmd("send", { conversationId: selectedId, text: body, attachments: files }, function(obj) {
      actionStatus = ""
      if (!obj.ok) {
        lastError = String(obj.error || "Send failed")
        return
      }
      lastError = ""
      loadMessages(selectedId)
      refreshConversations()
    })
  }

  function ensureCli() {
    actionStatus = "Downloading signal-cli (one-time, ~110 MB)…"
    sendCmd("ensureCli", {}, function(obj) {
      actionStatus = ""
      if (!obj.ok) lastError = String(obj.error || "Could not install signal-cli")
      else {
        cliInstalled = true
        lastError = ""
      }
    })
  }

  function startLink() {
    linking = true
    lastError = ""
    actionStatus = "Starting device link…"
    function begin() {
      sendCmd("startLink", { deviceName: "Omarchy" }, function(obj) {
        actionStatus = ""
        if (!obj.ok || !obj.result || obj.result.ok === false) {
          linking = false
          lastError = String((obj.result && obj.result.error) || obj.error || "Could not start linking")
          return
        }
        linkUri = String(obj.result.uri || "")
        linkQr = String(obj.result.qr || "")
        linkPoll.restart()
      })
    }
    if (!cliInstalled) {
      sendCmd("ensureCli", {}, function(obj) {
        if (!obj.ok) {
          linking = false
          lastError = String(obj.error || "Could not install signal-cli")
          return
        }
        cliInstalled = true
        begin()
      })
    } else begin()
  }

  function startDaemon() {
    if (daemon || daemonStarting) return
    daemonStarting = true
    sendCmd("startDaemon", {}, function(obj) {
      daemonStarting = false
      var ok = !!(obj.ok && obj.result && obj.result.ok === true)
      daemon = ok
      if (linked) canSend = true
      if (ok) {
        if (lastError.indexOf("socket") >= 0 || lastError.indexOf("Daemon") >= 0)
          lastError = ""
        actionStatus = ""
      } else if (linked) {
        actionStatus = "Connecting to Signal…"
        daemonRetry.restart()
      } else {
        lastError = String((obj.result && obj.result.error) || obj.error || "Daemon failed")
      }
    })
  }

  Timer {
    id: linkPoll
    interval: 1500
    repeat: true
    onTriggered: {
      sendCmd("linkStatus", {}, function(obj) {
        if (!obj.ok || !obj.result) return
        var st = obj.result
        if (st.linked) {
          linking = false
          linked = true
          canSend = true
          accounts = st.accounts || []
          linkPoll.stop()
          startDaemon()
          refresh()
        } else if (st.error && !st.running) {
          linking = false
          lastError = String(st.error)
          linkPoll.stop()
        }
      })
    }
  }

  Timer {
    interval: 8000
    running: true
    repeat: true
    onTriggered: if (!windowOpen) refreshConversations()
  }

  Timer {
    id: daemonRetry
    interval: 4000
    repeat: false
    onTriggered: if (linked && !daemon && !daemonStarting) startDaemon()
  }

  Process {
    id: engine
    running: true
    stdinEnabled: true
    command: [root.systemPython, root.enginePath, "serve"]
    stdout: SplitParser {
      splitMarker: "\n"
      onRead: function(line) { root.handleLine(line) }
    }
    stderr: StdioCollector { waitForEnd: false }
    onStarted: {
      root.lastError = ""
      root.ready = false
    }
    onExited: function(code) {
      root.ready = false
      // Plugin reloads and SIGTERM restarts are normal. Only surface a real crash.
      var crashed = code !== 0 && code !== 15 && code !== 9 && code !== 143
      if (crashed) root.lastError = "Signal engine exited"
      restartTimer.restart()
    }
  }

  Timer {
    id: restartTimer
    interval: 1200
    onTriggered: engine.running = true
  }

  Component.onCompleted: refresh()
}
