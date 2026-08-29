import QtQuick
import qs.Commons
import qs.Ui
import "Model.js" as Model

// Own Repeater so media `modelData` is not the parent message from the thread
// ListView. Photos, video stills, and link cards sit in the bubble like Signal.
Column {
  id: root

  property var items: []
  property color foreground: Color.foreground
  property color dim: Color.muted
  property color accent: Color.accent
  property string fontFamily: Style.font.family

  width: parent ? parent.width : implicitWidth
  spacing: Style.space(6)
  visible: items && items.length > 0

  function blockHeight(item, width) {
    var w = Number(item && item.width || 0)
    var h = Number(item && item.height || 0)
    if (w > 0 && h > 0) return Math.min(Style.space(340), Math.max(Style.space(96), width * h / w))
    return Style.space(200)
  }

  function openUrl(url) {
    var href = String(url || "")
    if (href !== "") Qt.openUrlExternally(href)
  }

  Repeater {
    model: root.items

    delegate: Item {
      id: block
      required property var modelData
      required property int index
      width: root.width
      implicitHeight: inner.implicitHeight

      readonly property string kind: String(modelData.kind || "")
      readonly property string imageUrl: String(modelData.url || modelData.thumb || "")
      readonly property string thumbUrl: String(modelData.thumb || modelData.url || "")
      readonly property bool isImage: kind === "image" || kind === "sticker"
      readonly property bool isVideo: kind === "video"
      readonly property bool isPreview: kind === "preview"

      Column {
        id: inner
        width: parent.width
        spacing: Style.space(4)

        Item {
          visible: block.isImage || block.isVideo
          width: parent.width
          height: root.blockHeight(block.modelData, width)
          clip: true

          Image {
            anchors.fill: parent
            fillMode: block.isVideo ? Image.PreserveAspectCrop : Image.PreserveAspectFit
            asynchronous: true
            cache: true
            source: block.isVideo ? block.thumbUrl : block.imageUrl
            sourceSize.width: 720
          }

          Rectangle {
            visible: block.isVideo
            anchors.fill: parent
            color: Qt.rgba(0, 0, 0, 0.22)
          }

          Rectangle {
            visible: block.isVideo
            anchors.centerIn: parent
            width: Style.space(40)
            height: width
            radius: width / 2
            color: Qt.rgba(0, 0, 0, 0.55)
            Text {
              anchors.centerIn: parent
              text: "▶"
              color: "#ffffff"
              font.pixelSize: Style.font.title
            }
          }

          MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: root.openUrl(block.imageUrl)
          }
        }

        Rectangle {
          visible: block.isPreview
          width: parent.width
          implicitHeight: previewCol.implicitHeight + Style.space(12)
          radius: Style.cornerRadius
          color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.06)
          border.width: 1
          border.color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.14)

          MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: root.openUrl(block.modelData.pageUrl || block.imageUrl)
          }

          Column {
            id: previewCol
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.margins: Style.space(8)
            spacing: Style.space(4)

            Image {
              visible: block.thumbUrl !== ""
              width: parent.width
              height: Math.min(Style.space(150), root.blockHeight(block.modelData, width))
              fillMode: Image.PreserveAspectCrop
              asynchronous: true
              source: block.thumbUrl
              sourceSize.width: 640
            }
            Text {
              visible: String(block.modelData.title || "") !== ""
              width: parent.width
              text: block.modelData.title || ""
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              font.bold: true
              wrapMode: Text.Wrap
              maximumLineCount: 2
              elide: Text.ElideRight
            }
            Text {
              visible: String(block.modelData.description || "") !== ""
              width: parent.width
              text: block.modelData.description || ""
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              wrapMode: Text.Wrap
              maximumLineCount: 3
              elide: Text.ElideRight
            }
            Text {
              width: parent.width
              text: Model.domainFromUrl(block.modelData.pageUrl || block.imageUrl)
              color: root.accent
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              elide: Text.ElideRight
            }
          }
        }

        Rectangle {
          visible: !block.isImage && !block.isVideo && !block.isPreview
          width: parent.width
          height: Style.space(40)
          radius: Style.cornerRadius
          color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.06)
          border.width: 1
          border.color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.14)

          MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: root.openUrl(block.imageUrl)
          }

          Row {
            anchors.fill: parent
            anchors.margins: Style.space(8)
            spacing: Style.space(8)
            Text {
              anchors.verticalCenter: parent.verticalCenter
              text: "󰈔"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.icon
            }
            Column {
              anchors.verticalCenter: parent.verticalCenter
              width: parent.width - Style.space(28)
              Text {
                width: parent.width
                text: block.modelData.name || (block.kind === "audio" ? "Audio" : "File")
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                elide: Text.ElideRight
              }
              Text {
                text: Model.formatBytes(block.modelData.size)
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }
            }
          }
        }
      }
    }
  }
}
