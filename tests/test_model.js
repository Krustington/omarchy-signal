const assert = require("assert")
const Model = require("../Model.js")

function testParseStatus() {
  const empty = Model.parseStatus("")
  assert.strictEqual(empty.installed, false)
  assert.strictEqual(empty.unreadCount, 0)

  const ok = Model.parseStatus(JSON.stringify({
    ok: true,
    installed: true,
    running: true,
    unreadCount: 2,
    conversations: [{ name: "Ada", preview: "Hi", timestamp: 1, unread: 1, count: 1 }]
  }))
  assert.strictEqual(ok.installed, true)
  assert.strictEqual(ok.unreadCount, 2)
  assert.strictEqual(ok.conversations.length, 1)

  const bad = Model.parseStatus("{not json")
  assert.strictEqual(bad.ok, false)
}

function testGroupConversations() {
  const notes = [
    { app: "Signal", summary: "Ada", body: "later", timestamp: 200 },
    { app: "Signal", summary: "Ada", body: "earlier", timestamp: 100 },
    { app: "Grok", summary: "Not Signal", body: "nope", timestamp: 300 },
    { app: "", appIcon: "signal-desktop", summary: "Bea", body: "hey", timestamp: 150 }
  ]
  const grouped = Model.groupConversations(notes, 120, 12)
  assert.strictEqual(grouped.length, 2)
  assert.strictEqual(grouped[0].name, "Ada")
  assert.strictEqual(grouped[0].preview, "later")
  assert.strictEqual(grouped[0].unread, 1)
  assert.strictEqual(grouped[0].count, 2)
  assert.strictEqual(grouped[1].name, "Bea")
  assert.strictEqual(grouped[1].unread, 1)
  assert.strictEqual(Model.unreadTotal(grouped), 2)
}

function testDisplayHelpers() {
  assert.strictEqual(Model.initials("Ada Lovelace"), "AL")
  assert.strictEqual(Model.initials("Ada"), "A")
  assert.strictEqual(Model.initials(""), "?")
  assert.strictEqual(Model.heroMeta({ installed: true, unreadCount: 3 }), "3 unread")
  assert.strictEqual(Model.heroMeta({ installed: true, running: true, unreadCount: 0 }), "Running")
  assert.strictEqual(Model.heroDetail({ installed: true, running: true, unreadCount: 3 }), "ON")
  assert.strictEqual(Model.themeLine({ themeName: "Duskwire", themeMode: "dark" }), "Following Duskwire · dark")
  assert.strictEqual(Model.openOnClickIsApp("App"), true)
  assert.strictEqual(Model.openOnClickIsApp("Panel"), false)
  assert.strictEqual(Model.markSeenOnOpen("Off"), false)
  assert.ok(Model.relativeTime(Date.now() - 1000).length > 0)
}

testParseStatus()
testGroupConversations()
testDisplayHelpers()
console.log("ok")
