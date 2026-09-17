import QtQuick
import QtQuick.Controls as QQC
import QtQuick.Layouts
import qs.Commons
import qs.Ui

// Add / edit form. Name is one line; Command and Help are multi-line so a
// long command line or a couple of paragraphs of help fit. Validation lives
// in the engine; its message is shown under the form.
Item {
  id: editor
  property string title: "New script"
  property string errorText: ""
  property color foreground: Color.foreground
  property color accent: Color.accent
  property color urgent: Color.urgent
  property color dim: Qt.darker(foreground, 1.55)
  property string fontFamily: Style.font.family
  property string monoFamily: Style.fontFamily

  readonly property bool hasFocus: nameField.activeFocus || commandField.activeFocus || helpField.activeFocus

  signal saveRequested(var fields)
  signal cancelRequested()

  function load(script) {
    nameField.text = script ? script.name : ""
    commandField.text = script ? script.command : ""
    helpField.text = script ? script.help : ""
    errorText = ""
    Qt.callLater(function() { nameField.forceActiveFocus() })
  }

  function fields() {
    return { name: nameField.text, command: commandField.text, help: helpField.text }
  }

  component FieldLabel: Text {
    Layout.fillWidth: true
    textFormat: Text.PlainText
    color: editor.dim
    font.family: editor.fontFamily
    font.pixelSize: Style.font.caption
  }

  // A bordered box around a multi-line editor, matching the kit's field look.
  component TextBox: Rectangle {
    property alias text: area.text
    property alias area: area
    property bool mono: false
    Layout.fillWidth: true
    Layout.preferredHeight: Style.space(88)
    radius: Style.cornerRadius
    color: Util.alpha(editor.foreground, area.activeFocus ? 0.06 : 0.03)
    border.width: 1
    border.color: area.activeFocus ? editor.accent : Util.alpha(editor.foreground, 0.2)

    Flickable {
      anchors.fill: parent
      anchors.margins: Style.space(6)
      clip: true
      contentWidth: width
      contentHeight: area.implicitHeight
      boundsBehavior: Flickable.StopAtBounds
      interactive: contentHeight > height

      QQC.TextArea {
        id: area
        width: parent.width
        wrapMode: TextEdit.WrapAnywhere
        selectByMouse: true
        color: editor.foreground
        selectionColor: Util.alpha(editor.accent, 0.4)
        font.family: mono ? editor.monoFamily : editor.fontFamily
        font.pixelSize: Style.font.body
        padding: 0
        background: null
        Keys.onEscapePressed: function(event) { editor.cancelRequested(); event.accepted = true }
      }
    }
  }

  ColumnLayout {
    anchors.fill: parent
    spacing: Style.space(6)

    Text {
      Layout.fillWidth: true
      text: editor.title
      textFormat: Text.PlainText
      color: editor.foreground
      font.family: editor.fontFamily
      font.pixelSize: Style.font.body
      font.bold: true
    }

    FieldLabel { text: "Name" }
    TextField {
      id: nameField
      Layout.fillWidth: true
      placeholderText: "Ports (all)"
      foreground: editor.foreground
      accent: editor.accent
      Keys.onEscapePressed: function(event) { editor.cancelRequested(); event.accepted = true }
      onAccepted: commandField.area.forceActiveFocus()
    }

    FieldLabel { text: "Command (runs in non-interactive bash, exactly as written)" }
    TextBox { id: commandField; mono: true }

    FieldLabel { text: "Help" }
    TextBox { id: helpField; Layout.fillHeight: true }

    Text {
      Layout.fillWidth: true
      visible: editor.errorText !== ""
      text: editor.errorText
      textFormat: Text.PlainText
      wrapMode: Text.Wrap
      color: editor.urgent
      font.family: editor.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    RowLayout {
      Layout.fillWidth: true
      spacing: Style.space(6)
      Item { Layout.fillWidth: true }
      Button {
        text: "Cancel"
        bordered: true
        foreground: editor.foreground
        fontFamily: editor.fontFamily
        onClicked: editor.cancelRequested()
      }
      Button {
        text: "Save"
        bordered: true
        foreground: editor.accent
        fontFamily: editor.fontFamily
        onClicked: editor.saveRequested(editor.fields())
      }
    }
  }
}
