import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "krusty.signal"
  clip: false

  readonly property var app: bar && bar.shell ? bar.shell.serviceFor("krusty.signal") : null
  readonly property color foreground: bar ? bar.barForeground : Color.foreground
  readonly property int unreadCount: app ? Math.max(0, Number(app.unreadCount || 0)) : 0
  readonly property bool hasUnread: unreadCount > 0
  readonly property bool connected: app ? app.desktop === true : false
  readonly property bool windowOpen: app ? app.windowOpen === true : false

  readonly property bool opened: windowOpen
  property bool popoutSwitchClosing: false

  function open() { openWindow() }
  function close() { closeWindow() }
  function toggle() { windowOpen ? closeWindow() : openWindow() }
  function togglePanel() { toggle() }
  function closeForPopoutSwitch() {
    popoutSwitchClosing = true
    close()
    Qt.callLater(function() { root.popoutSwitchClosing = false })
  }

  function openWindow() {
    if (!bar || !bar.shell) return
    if (typeof bar.shell.summon === "function") bar.shell.summon("krusty.signal", "{}")
    else if (typeof bar.shell.toggle === "function") bar.shell.toggle("krusty.signal", "{}")
  }

  function closeWindow() {
    if (!bar || !bar.shell) return
    if (typeof bar.shell.hide === "function") bar.shell.hide("krusty.signal")
  }

  function openDesktop() {
    if (root.app && typeof root.app.showDesktop === "function") root.app.showDesktop()
    else if (bar) bar.run("omarchy-launch-signal")
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    tooltipText: app ? app.barTooltip : "Signal"
    readonly property color glyphColor: root.connected
      ? root.foreground
      : Qt.darker(root.foreground, 1.55)

    iconComponent: Component {
      Item {
        clip: false
        SignalIcon {
          anchors.centerIn: parent
          iconSize: Style.space(12)
          color: button.glyphColor
          markColor: Color.accent
          dimmed: !root.connected
        }
      }
    }

    onPressed: function(buttonCode) {
      if (buttonCode === Qt.MiddleButton) {
        if (root.app) root.app.refresh()
        return
      }
      if (buttonCode === Qt.RightButton) {
        root.openDesktop()
        return
      }
      root.toggle()
    }
  }

  // Sit on the bar slot, not inside the 12px optical canvas — that canvas
  // clips a corner pip so it never reads as "you have mail".
  Rectangle {
    id: unreadBadge
    visible: root.hasUnread
    z: 20
    width: Math.max(height, badgeLabel.visible ? badgeLabel.implicitWidth + Style.space(5) : height)
    height: badgeLabel.visible ? Style.space(11) : Style.space(8)
    radius: height / 2
    color: Color.accent
    border.width: 1
    border.color: Color.bar.background
    anchors.right: parent.right
    anchors.top: parent.top
    anchors.rightMargin: Style.space(1)
    anchors.topMargin: Style.space(1)

    Text {
      id: badgeLabel
      visible: root.unreadCount > 1
      anchors.centerIn: parent
      text: root.unreadCount > 9 ? "9+" : String(root.unreadCount)
      color: Color.background
      font.family: root.bar ? root.bar.fontFamily : Style.font.family
      font.pixelSize: 8
      font.bold: true
    }
  }

  Rectangle {
    visible: root.windowOpen
    color: Color.accent
    radius: Math.min(width, height) / 2
    width: root.vertical ? Style.space(2) : Style.space(10)
    height: root.vertical ? Style.space(10) : Style.space(2)
    x: root.vertical
      ? (root.bar && root.bar.position === "left" ? root.width - width - Style.space(2) : Style.space(2))
      : Math.round((root.width - width) / 2)
    y: root.vertical
      ? Math.round((root.height - height) / 2)
      : (root.bar && root.bar.position === "top"
        ? root.height - height - Style.space(2) : Style.space(2))
  }
}
