import QtQuick
import QtQuick.Effects
import qs.Commons
import "Model.js" as Model

Item {
  id: root

  property string source: ""
  property string name: ""
  property color foreground
  property color accent
  property int avatarSize: Style.space(28)

  width: avatarSize
  height: avatarSize

  Rectangle {
    anchors.fill: parent
    radius: width / 2
    color: Style.selectedFillFor(root.foreground, root.accent)

    Text {
      anchors.centerIn: parent
      visible: pic.status !== Image.Ready
      text: Model.initials(root.name)
      color: root.accent
      font.family: Style.font.family
      font.pixelSize: Math.max(Style.font.caption, Math.round(root.avatarSize * 0.36))
      font.bold: true
    }
  }

  Image {
    id: pic
    anchors.fill: parent
    source: root.source
    fillMode: Image.PreserveAspectCrop
    asynchronous: true
    cache: true
    visible: false
    sourceSize.width: root.avatarSize * 2
    sourceSize.height: root.avatarSize * 2
  }

  Rectangle {
    id: mask
    anchors.fill: parent
    radius: width / 2
    visible: false
    color: "white"
    layer.enabled: true
  }

  MultiEffect {
    anchors.fill: parent
    source: pic
    maskEnabled: true
    maskSource: mask
    maskThresholdMin: 0.5
    visible: pic.status === Image.Ready
  }
}
