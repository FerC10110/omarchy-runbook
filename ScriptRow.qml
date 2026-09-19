import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Ui

// One script in the list. The small ▶ is the only thing that runs it; the rest
// of the row only selects. The glyph on the right is the session state:
// ● running, ✓ 0 finished well, ✗ N finished with an error, nothing otherwise.
// The same row shows one of Omarchy's own commands in the Omarchy view, and
// draws a separator ("──── docker ────") when the item is one.
CursorSurface {
  id: row
  required property var modelData
  required property int index
  property bool selected: false
  property var session: null            // { dead: bool, exit: int|null } | null
  // foreground and accent are inherited from CursorSurface.
  property color urgent: Color.urgent
  property string fontFamily: Style.font.family

  signal play()
  signal select()

  readonly property var script: modelData
  readonly property bool isSeparator: script.kind === "separator"
  readonly property bool alive: session !== null && session.dead === false
  readonly property string statusText: session === null ? ""
    : (alive ? "●" : (session.exit === 0 ? "✓ 0" : "✗ " + (session.exit === null ? "?" : session.exit)))
  readonly property color statusColor: alive ? accent
    : (session !== null && session.exit === 0 ? Qt.darker(foreground, 1.55) : urgent)

  width: ListView.view ? ListView.view.width : 0
  height: Style.space(30)
  current: selected
  hasCursor: rowMouse.containsMouse

  // Declared before the layout so the ▶ button stays on top of it.
  MouseArea {
    id: rowMouse
    anchors.fill: parent
    hoverEnabled: true
    onClicked: row.select()
  }

  // A separator row: selectable (to edit, move or delete it) but never run.
  SeparatorLine {
    anchors.fill: parent
    anchors.leftMargin: Style.space(8)
    anchors.rightMargin: Style.space(8)
    visible: row.isSeparator
    label: row.isSeparator ? row.script.label : ""
    color: Qt.darker(row.foreground, 1.55)
    fontFamily: row.fontFamily
  }

  RowLayout {
    anchors.fill: parent
    anchors.leftMargin: Style.space(4)
    anchors.rightMargin: Style.space(8)
    spacing: Style.space(6)
    visible: !row.isSeparator

    PanelActionButton {
      iconText: "▶"
      size: Style.space(24)
      fontSize: Style.font.bodySmall
      tooltipText: row.alive ? "Show terminal" : "Run"
      foreground: row.foreground
      hoverColor: row.accent
      onClicked: row.play()
    }

    Text {
      Layout.fillWidth: true
      text: row.isSeparator ? "" : row.script.name
      textFormat: Text.PlainText
      elide: Text.ElideRight
      color: row.foreground
      font.family: row.fontFamily
      font.pixelSize: Style.font.body
    }

    Text {
      visible: row.script.sudo === true
      text: "sudo"
      textFormat: Text.PlainText
      color: Qt.darker(row.foreground, 1.55)
      font.family: row.fontFamily
      font.pixelSize: Style.font.caption
    }

    Text {
      visible: row.script.source === "readily"
      text: "◆"
      textFormat: Text.PlainText
      color: Qt.darker(row.foreground, 1.55)
      font.family: row.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    Text {
      visible: row.script.schedule ? true : false
      text: "⏰"
      textFormat: Text.PlainText
      color: Qt.darker(row.foreground, 1.55)
      font.family: row.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    Text {
      visible: text !== ""
      text: row.statusText
      textFormat: Text.PlainText
      color: row.statusColor
      font.family: row.fontFamily
      font.pixelSize: Style.font.bodySmall
    }
  }
}
