import QtQuick
import QtQuick.Layouts
import qs.Commons

// A separator as the list draws it: a line across, with the label centred on
// it when there is one ("──── docker ────").
Item {
  id: line
  property string label: ""
  property color color: Qt.darker(Color.foreground, 1.55)
  property string fontFamily: Style.font.family

  implicitHeight: labelText.implicitHeight

  RowLayout {
    anchors.fill: parent
    spacing: Style.space(8)

    Rectangle {
      Layout.fillWidth: true
      Layout.alignment: Qt.AlignVCenter
      implicitHeight: 1
      color: Util.alpha(line.color, 0.6)
    }

    Text {
      id: labelText
      visible: line.label !== ""
      Layout.maximumWidth: line.width * 0.7
      text: line.label
      textFormat: Text.PlainText
      elide: Text.ElideRight
      color: line.color
      font.family: line.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    Rectangle {
      visible: line.label !== ""
      Layout.fillWidth: true
      Layout.alignment: Qt.AlignVCenter
      implicitHeight: 1
      color: Util.alpha(line.color, 0.6)
    }
  }
}
