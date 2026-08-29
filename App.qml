import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

Item {
  id: root

  property var shell: null
  property var manifest: null
  property var service: null
  property bool opened: false
  property bool closingFromHost: false

  readonly property color foreground: Color.foreground
  readonly property color background: Color.background
  readonly property color accent: Color.accent
  readonly property color urgent: Color.urgent
  readonly property color dim: Qt.rgba(
    foreground.r * 0.68 + background.r * 0.32,
    foreground.g * 0.68 + background.g * 0.32,
    foreground.b * 0.68 + background.b * 0.32, 1)
  readonly property string fontFamily: Style.font.family
  readonly property var convos: service ? service.conversations : []
  readonly property var messages: service ? service.messages : []
  readonly property var current: service ? service.selected : null

  // One classic mouse notch is 120. High-res wheels send many smaller
  // angleDeltas for the same notch — scale by size, never by "any tick
  // = a page." ~36px per notch is two conversation rows.
  function scrollList(view, event) {
    if (!view || !event) return
    event.accepted = true
    var ad = event.angleDelta ? event.angleDelta.y : 0
    var pd = event.pixelDelta ? event.pixelDelta.y : 0
    var dy = 0
    if (ad !== 0)
      dy = ad * (36 / 120)
    else if (pd !== 0)
      dy = pd * 0.5
    if (dy === 0) return
    var maxY = Math.max(0, view.contentHeight - view.height)
    view.contentY = Math.max(0, Math.min(maxY, view.contentY - dy))
  }

  Connections {
    target: service
    function onLinkedChanged() {
      if (service && service.linked) setup.visible = false
    }
    function onSelectedIdChanged() {
      thread.stickToEnd = true
    }
    function onMessagesChanged() {
      if (thread.stickToEnd)
        Qt.callLater(function() {
          thread.contentY = Math.max(0, thread.contentHeight - thread.height)
          thread.stickToEnd = false
        })
    }
  }

  function open() {
    opened = true
    closingFromHost = false
    if (service) {
      service.windowOpen = true
      service.refresh()
    }
  }

  function close() {
    closingFromHost = true
    opened = false
    if (service) service.windowOpen = false
  }

  function requestClose() {
    if (shell && typeof shell.hide === "function") shell.hide("krusty.signal")
    else close()
  }

  function sendDraft() {
    if (!service || !composer.hasText) return
    var paths = []
    for (var i = 0; i < composer.files.length; i++) paths.push(composer.files[i].path)
    service.sendMessage(composer.draft, paths)
    composer.clear()
  }

  Process {
    id: attachPicker
    command: ["zenity", "--file-selection", "--multiple", "--separator=\n", "--title=Attach to Signal"]
    stdout: StdioCollector {
      id: attachOut
      waitForEnd: true
    }
    onExited: function(code) {
      if (code !== 0) return
      var lines = String(attachOut.text || "").split("\n")
      for (var i = 0; i < lines.length; i++) {
        var p = lines[i].trim()
        if (p !== "") composer.addFile(p)
      }
    }
  }

  FloatingWindow {
    id: window
    visible: root.opened
    title: current ? ("Signal — " + current.name) : "Signal"
    color: root.background
    implicitWidth: Style.space(1100)
    implicitHeight: Style.space(740)
    minimumSize: Qt.size(Style.space(880), Style.space(560))

    onVisibleChanged: {
      if (!visible && root.opened && !root.closingFromHost) root.requestClose()
    }
    Component.onDestruction: visible = false

    Row {
      anchors.fill: parent
      spacing: 0

      Rectangle {
        id: sidebar
        width: Style.space(320)
        height: parent.height
        color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.04)

        Column {
          anchors.fill: parent
          anchors.margins: Style.space(12)
          spacing: Style.space(10)

          RowLayout {
            width: parent.width
            spacing: Style.space(8)
            SignalIcon {
              iconSize: Style.space(18)
              color: root.foreground
              markColor: root.accent
              Layout.alignment: Qt.AlignVCenter
            }
            Text {
              text: "Signal"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.title
              font.bold: true
              Layout.fillWidth: true
            }
            Text {
              visible: service && service.unreadCount > 0
              text: service ? String(service.unreadCount) : ""
              color: root.accent
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
            }
          }

          TextField {
            id: searchField
            width: parent.width
            placeholderText: "Search conversations"
            foreground: root.foreground
            accent: root.accent
            onTextChanged: if (service) service.search(text)
          }

          Item {
            width: parent.width
            height: parent.height - y

          ListView {
            id: convoList
            anchors.fill: parent
            anchors.rightMargin: Style.space(12)
            clip: true
            spacing: Style.space(2)
            boundsBehavior: Flickable.StopAtBounds
            cacheBuffer: Style.space(800)
            flickDeceleration: 2400
            maximumFlickVelocity: 8000
            reuseItems: true
            model: root.convos
            flickableDirection: Flickable.VerticalFlick
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AlwaysOff }
            WheelHandler {
              acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
              grabPermissions: PointerHandler.CanTakeOverFromAnything
              blocking: true
              onWheel: function(event) { root.scrollList(convoList, event) }
            }

            currentIndex: {
              for (var i = 0; i < root.convos.length; i++)
                if (root.current && root.convos[i].id === root.current.id) return i
              return -1
            }

            delegate: CursorSurface {
              required property var modelData
              required property int index
              width: convoList.width
              implicitHeight: row.implicitHeight + Style.space(12)
              hasCursor: convoList.currentIndex === index
              current: service && service.selectedId === modelData.id
              foreground: root.foreground
              accent: root.accent

              MouseArea {
                anchors.fill: parent
                z: 2
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                preventStealing: true
                onClicked: if (service) service.openConversation(modelData.id)
              }

              RowLayout {
                id: row
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: Style.space(8)
                anchors.rightMargin: Style.space(8)
                spacing: Style.space(10)

                Avatar {
                  avatarSize: Style.space(28)
                  source: modelData.avatar || ""
                  name: modelData.name || ""
                  foreground: root.foreground
                  accent: root.accent
                  Layout.alignment: Qt.AlignVCenter
                }

                ColumnLayout {
                  Layout.fillWidth: true
                  spacing: 1
                  RowLayout {
                    Layout.fillWidth: true
                    Text {
                      Layout.fillWidth: true
                      text: modelData.name
                      color: root.foreground
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.body
                      font.bold: Number(modelData.unread || 0) > 0
                      elide: Text.ElideRight
                    }
                    Text {
                      text: Model.relativeTime(modelData.timestamp)
                      color: root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                    }
                  }
                  Text {
                    Layout.fillWidth: true
                    text: modelData.preview || ""
                    color: Number(modelData.unread || 0) > 0 ? root.foreground : root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    elide: Text.ElideRight
                  }
                }

                Rectangle {
                  visible: Number(modelData.unread || 0) > 0
                  width: Style.space(7)
                  height: Style.space(7)
                  radius: width / 2
                  color: root.accent
                  Layout.alignment: Qt.AlignVCenter
                }
              }
            }
          }

            Rectangle {
              id: convoScroll
              visible: convoList.contentHeight > convoList.height + 8
              anchors.right: parent.right
              anchors.top: parent.top
              anchors.bottom: parent.bottom
              z: 3
              width: Style.space(10)
              radius: width / 2
              color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.10)

              MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: function(mouse) {
                  var span = Math.max(1, convoScroll.height - convoThumb.height)
                  var y = Math.max(0, Math.min(span, mouse.y - convoThumb.height / 2))
                  convoList.contentY = y / span * Math.max(0, convoList.contentHeight - convoList.height)
                }
              }

              Rectangle {
                id: convoThumb
                width: parent.width
                height: Math.max(Style.space(24), parent.height * convoList.height / Math.max(1, convoList.contentHeight))
                y: {
                  var span = Math.max(1, convoList.contentHeight - convoList.height)
                  return (parent.height - height) * convoList.contentY / span
                }
                radius: width / 2
                color: root.accent

                MouseArea {
                  anchors.fill: parent
                  cursorShape: Qt.SizeVerCursor
                  drag.target: parent
                  drag.axis: Drag.YAxis
                  drag.minimumY: 0
                  drag.maximumY: Math.max(0, convoThumb.parent.height - convoThumb.height)
                  onClicked: function(mouse) { mouse.accepted = true }
                  onPositionChanged: {
                    if (!pressed) return
                    var span = convoThumb.parent.height - convoThumb.height
                    if (span > 0)
                      convoList.contentY = convoThumb.y / span * Math.max(0, convoList.contentHeight - convoList.height)
                  }
                }
              }
            }
          }
        }
      }

      Rectangle {
        width: 1
        height: parent.height
        color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.12)
      }

      Item {
        id: main
        width: parent.width - sidebar.width - 1
        height: parent.height

        Column {
          anchors.fill: parent
          visible: current !== null
          spacing: 0

          Rectangle {
            width: parent.width
            height: Style.space(52)
            color: "transparent"
            RowLayout {
              anchors.fill: parent
              anchors.leftMargin: Style.space(16)
              anchors.rightMargin: Style.space(16)
              spacing: Style.space(10)
              Avatar {
                avatarSize: Style.space(32)
                source: current && current.avatar ? current.avatar : ""
                name: current ? current.name : ""
                foreground: root.foreground
                accent: root.accent
                Layout.alignment: Qt.AlignVCenter
              }
              Text {
                text: current ? current.name : ""
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.title
                font.bold: true
                Layout.fillWidth: true
                elide: Text.ElideRight
              }
              Text {
                text: current && current.kind === "group" ? "Group" : (current && current.e164 ? current.e164 : "")
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }
            }
            Rectangle {
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.bottom: parent.bottom
              height: 1
              color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.12)
            }
          }

          Item {
            width: parent.width
            height: parent.height - Style.space(52) - composer.height - banner.height

            Flickable {
              id: thread
              anchors.fill: parent
              anchors.rightMargin: Style.space(12)
              clip: true
              boundsBehavior: Flickable.StopAtBounds
              flickableDirection: Flickable.VerticalFlick
              flickDeceleration: 2400
              maximumFlickVelocity: 8000
              contentWidth: width
              contentHeight: threadCol.implicitHeight + Style.space(24)
              property bool stickToEnd: true
              WheelHandler {
                acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
                grabPermissions: PointerHandler.CanTakeOverFromAnything
                blocking: true
                onWheel: function(event) { root.scrollList(thread, event) }
              }

              Column {
                id: threadCol
                width: thread.width
                spacing: Style.space(8)

                Item { width: 1; height: Style.space(8) }

                Repeater {
                  model: root.messages

                  delegate: Item {
                    required property var modelData
                    required property int index
                    width: threadCol.width
                    implicitHeight: bubble.implicitHeight

                    readonly property bool outgoing: modelData.outgoing === true
                    readonly property var media: Model.mediaList(modelData)
                    readonly property bool hasMedia: media.length > 0

                    Rectangle {
                      id: bubble
                      anchors.left: outgoing ? undefined : parent.left
                      anchors.right: outgoing ? parent.right : undefined
                      anchors.leftMargin: Style.space(16)
                      anchors.rightMargin: Style.space(16)
                      width: Math.min(thread.width * 0.78, Math.max(
                        hasMedia ? Style.space(280) : Style.space(80),
                        bodyText.implicitWidth + Style.space(24)))
                      implicitHeight: bodyCol.implicitHeight + Style.space(16)
                      radius: Style.cornerRadius
                      color: outgoing
                        ? Style.selectedFillFor(root.foreground, root.accent)
                        : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.06)
                      border.width: outgoing ? 0 : 1
                      border.color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.12)

                      Column {
                        id: bodyCol
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.leftMargin: Style.space(10)
                        anchors.rightMargin: Style.space(10)
                        spacing: Style.space(6)

                        Text {
                          visible: String(modelData.quote || "") !== ""
                          width: parent.width
                          text: modelData.quote
                          color: root.dim
                          font.family: root.fontFamily
                          font.pixelSize: Style.font.caption
                          wrapMode: Text.Wrap
                        }

                        MediaStack {
                          width: parent.width
                          items: media
                          foreground: root.foreground
                          dim: root.dim
                          accent: root.accent
                          fontFamily: root.fontFamily
                        }

                        Text {
                          id: bodyText
                          visible: String(modelData.body || "") !== ""
                          width: parent.width
                          text: modelData.body
                          color: root.foreground
                          font.family: root.fontFamily
                          font.pixelSize: Style.font.body
                          wrapMode: Text.Wrap
                          onLinkActivated: function(link) { Qt.openUrlExternally(link) }
                        }
                        Text {
                          visible: !bodyText.visible && !hasMedia
                          width: parent.width
                          text: "Attachment"
                          color: root.dim
                          font.family: root.fontFamily
                          font.pixelSize: Style.font.body
                        }
                        Text {
                          text: Model.relativeTime(modelData.timestamp)
                          color: root.dim
                          font.family: root.fontFamily
                          font.pixelSize: Style.font.caption
                          anchors.right: parent.right
                        }
                      }
                    }
                  }
                }
              }
            }

            Rectangle {
              visible: thread.contentHeight > thread.height + 8
              anchors.right: parent.right
              anchors.top: parent.top
              anchors.bottom: parent.bottom
              z: 3
              width: Style.space(10)
              radius: width / 2
              color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.10)

              Rectangle {
                id: threadThumb
                width: parent.width
                height: Math.max(Style.space(28), parent.height * thread.height / Math.max(1, thread.contentHeight))
                y: {
                  var span = Math.max(1, thread.contentHeight - thread.height)
                  return (parent.height - height) * thread.contentY / span
                }
                radius: width / 2
                color: root.accent

                MouseArea {
                  anchors.fill: parent
                  drag.target: parent
                  drag.axis: Drag.YAxis
                  drag.minimumY: 0
                  drag.maximumY: Math.max(0, threadThumb.parent.height - threadThumb.height)
                  onPositionChanged: {
                    var span = threadThumb.parent.height - threadThumb.height
                    if (span > 0)
                      thread.contentY = threadThumb.y / span * Math.max(0, thread.contentHeight - thread.height)
                  }
                }
              }
            }
          }

          Rectangle {
            id: banner
            width: parent.width
            height: visible ? Style.space(42) : 0
            visible: service && !service.linked && !service.canSend
            color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.12)
            RowLayout {
              anchors.fill: parent
              anchors.leftMargin: Style.space(16)
              anchors.rightMargin: Style.space(12)
              Text {
                Layout.fillWidth: true
                text: "Link Omarchy as a Signal device to reply from this window."
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                elide: Text.ElideRight
              }
              Button {
                text: "Link device"
                foreground: root.foreground
                accent: root.accent
                onClicked: setup.visible = true
              }
            }
          }

          Item {
            id: composer
            width: parent.width
            height: Style.space(72) + (files.length > 0 ? Style.space(32) : 0)
            property alias draft: draftField.text
            property var files: []
            readonly property bool hasText: draftField.text.trim() !== "" || files.length > 0
            function clear() { draftField.text = ""; files = [] }
            function addFile(path) {
              var name = String(path || "").split("/").pop()
              if (name === "") return
              var next = files.slice()
              next.push({ path: path, name: name })
              files = next
            }
            function removeFile(index) {
              var next = files.slice()
              next.splice(index, 1)
              files = next
            }

            Column {
              anchors.fill: parent
              anchors.margins: Style.space(10)
              spacing: Style.space(6)

              Row {
                visible: composer.files.length > 0
                width: parent.width
                spacing: Style.space(6)
                Repeater {
                  model: composer.files
                  delegate: Rectangle {
                    required property var modelData
                    required property int index
                    height: Style.space(22)
                    width: chipLabel.implicitWidth + Style.space(28)
                    radius: height / 2
                    color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.10)
                    Text {
                      id: chipLabel
                      anchors.left: parent.left
                      anchors.verticalCenter: parent.verticalCenter
                      anchors.leftMargin: Style.space(8)
                      text: modelData.name
                      color: root.foreground
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                      elide: Text.ElideMiddle
                      width: Math.min(implicitWidth, Style.space(160))
                    }
                    MouseArea {
                      anchors.right: parent.right
                      anchors.verticalCenter: parent.verticalCenter
                      width: Style.space(18)
                      height: width
                      onClicked: composer.removeFile(index)
                      Text {
                        anchors.centerIn: parent
                        text: "×"
                        color: root.dim
                        font.pixelSize: Style.font.body
                      }
                    }
                  }
                }
              }

              RowLayout {
                width: parent.width
                height: Style.space(48)
                spacing: Style.space(8)

                Button {
                  text: "+"
                  tooltipText: "Attach a photo or file"
                  enabled: !!(service && service.canSend)
                  foreground: root.foreground
                  accent: root.accent
                  onClicked: { attachPicker.running = true }
                }

                TextArea {
                  id: draftField
                  Layout.fillWidth: true
                  Layout.fillHeight: true
                  wrapMode: TextArea.Wrap
                  placeholderText: service && service.canSend ? "Write a message" : "Link a device to reply"
                  enabled: !!(service && service.canSend)
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  background: Rectangle {
                    radius: Style.cornerRadius
                    color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.05)
                    border.width: draftField.activeFocus ? Style.hoverBorderWidth : Style.normalBorderWidth
                    border.color: draftField.activeFocus ? root.accent : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.18)
                  }
                  Keys.onPressed: function(event) {
                    if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && !(event.modifiers & Qt.ShiftModifier)) {
                      event.accepted = true
                      root.sendDraft()
                    }
                  }
                }

                Button {
                  text: "Send"
                  enabled: composer.hasText && !!(service && service.canSend)
                  foreground: root.foreground
                  accent: root.accent
                  onClicked: root.sendDraft()
                }
              }
            }
          }
        }

        Column {
          anchors.centerIn: parent
          spacing: Style.space(10)
          visible: current === null && !setup.visible
          width: Math.min(Style.space(420), parent.width - Style.space(40))
          SignalIcon {
            anchors.horizontalCenter: parent.horizontalCenter
            iconSize: Style.space(36)
            color: root.foreground
            markColor: root.accent
          }
          Text {
            width: parent.width
            text: service && service.desktop ? "Select a conversation" : "Signal Desktop is not set up"
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.title
            font.bold: true
            horizontalAlignment: Text.AlignHCenter
          }
          Text {
            width: parent.width
            text: service && service.desktop
              ? (service.conversationCount + " chats. Photos, videos, and links load in the thread. Use + to attach.")
              : "Open Signal Desktop once so Omarchy can read your local message database."
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
          }
        }

        Rectangle {
          id: setup
          anchors.fill: parent
          visible: false
          color: root.background

          Column {
            anchors.centerIn: parent
            width: Math.min(Style.space(420), parent.width - Style.space(40))
            spacing: Style.space(12)

            Text {
              width: parent.width
              text: service && service.linked ? "Device linked" : "Link this device"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.title
              font.bold: true
              horizontalAlignment: Text.AlignHCenter
            }
            Text {
              width: parent.width
              text: "Scan this QR code in Signal on your phone: Settings → Linked devices → Link new device. History still comes from Signal Desktop on this computer; the link is only for sending."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              wrapMode: Text.WordWrap
              horizontalAlignment: Text.AlignHCenter
            }
            Image {
              visible: service && service.linkQr !== ""
              anchors.horizontalCenter: parent.horizontalCenter
              width: Style.space(220)
              height: Style.space(220)
              fillMode: Image.PreserveAspectFit
              source: service && service.linkQr !== "" ? "file://" + service.linkQr : ""
            }
            Text {
              width: parent.width
              visible: service && service.actionStatus !== ""
              text: service ? service.actionStatus : ""
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              wrapMode: Text.WordWrap
              horizontalAlignment: Text.AlignHCenter
            }
            Text {
              width: parent.width
              visible: service && service.lastError !== ""
              text: service ? service.lastError : ""
              color: root.urgent
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              wrapMode: Text.WordWrap
              horizontalAlignment: Text.AlignHCenter
            }
            Row {
              anchors.horizontalCenter: parent.horizontalCenter
              spacing: Style.space(8)
              Button {
                visible: !(service && service.linked)
                text: service && service.linking ? "Waiting for phone…" : "Show QR code"
                enabled: !(service && service.linking)
                foreground: root.foreground
                accent: root.accent
                onClicked: if (service) service.startLink()
              }
              Button {
                text: service && service.linked ? "Done" : "Close"
                foreground: root.foreground
                onClicked: setup.visible = false
              }
            }
          }
        }
      }
    }

    Text {
      anchors.left: parent.left
      anchors.bottom: parent.bottom
      anchors.margins: Style.space(8)
      visible: service && service.lastError !== "" && !setup.visible
      text: service ? service.lastError : ""
      color: root.urgent
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
    }
  }
}
