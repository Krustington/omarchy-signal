import QtQuick
import qs.Commons

// Signal's speech-bubble-and-slash, drawn rather than rasterised so a 12px
// bar slot stays crisp and the mark can wear every Omarchy theme. The bubble
// follows the bar foreground. The slash follows the theme accent — the same
// split GmailIcon uses for its envelope and M.
Item {
  id: root

  property real iconSize: Style.font.icon
  property color color: Color.foreground
  property color markColor: Color.accent
  property color badgeColor: Color.urgent
  property bool dot: false
  property bool dimmed: false

  width: iconSize
  height: iconSize
  implicitWidth: iconSize
  implicitHeight: iconSize

  onColorChanged: glyph.requestPaint()
  onMarkColorChanged: glyph.requestPaint()
  onIconSizeChanged: glyph.requestPaint()
  onDimmedChanged: glyph.requestPaint()

  Canvas {
    id: glyph
    anchors.fill: parent
    antialiasing: true

    onPaint: {
      var ctx = getContext("2d")
      ctx.reset()
      var w = width
      var h = height
      if (w <= 0 || h <= 0) return

      var stroke = Math.max(1.15, w * 0.09)
      var left = w * 0.14
      var right = w * 0.88
      var top = h * 0.10
      var bottom = h * 0.72
      var radius = Math.min((right - left), (bottom - top)) * 0.28

      ctx.strokeStyle = root.color
      ctx.fillStyle = "transparent"
      ctx.lineWidth = stroke
      ctx.lineJoin = "round"
      ctx.lineCap = "round"

      ctx.beginPath()
      ctx.moveTo(left + radius, top)
      ctx.lineTo(right - radius, top)
      ctx.quadraticCurveTo(right, top, right, top + radius)
      ctx.lineTo(right, bottom - radius)
      ctx.quadraticCurveTo(right, bottom, right - radius, bottom)
      ctx.lineTo(left + w * 0.42, bottom)
      ctx.lineTo(left + w * 0.08, h * 0.92)
      ctx.lineTo(left + w * 0.20, bottom)
      ctx.lineTo(left + radius, bottom)
      ctx.quadraticCurveTo(left, bottom, left, bottom - radius)
      ctx.lineTo(left, top + radius)
      ctx.quadraticCurveTo(left, top, left + radius, top)
      ctx.closePath()
      ctx.stroke()

      // The Signal slash: a single accent stroke through the bubble, inset
      // so it never collides with the outline at bar size.
      ctx.strokeStyle = root.dimmed ? root.color : root.markColor
      ctx.lineWidth = Math.max(1.2, stroke * 1.15)
      ctx.beginPath()
      ctx.moveTo(left + (right - left) * 0.22, bottom - (bottom - top) * 0.18)
      ctx.lineTo(right - (right - left) * 0.18, top + (bottom - top) * 0.22)
      ctx.stroke()
    }
  }

  Rectangle {
    visible: root.dot
    width: Math.max(5, root.iconSize * 0.28)
    height: width
    radius: width / 2
    color: root.badgeColor
    anchors.right: parent.right
    anchors.top: parent.top
    anchors.rightMargin: -1
    anchors.topMargin: -1
    border.width: 1
    border.color: Color.bar.background
  }
}
