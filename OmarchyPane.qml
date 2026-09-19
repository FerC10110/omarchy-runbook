import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Ui

// One of Omarchy's own commands: its usage line, what it does, the examples
// Omarchy ships with it and a line for the arguments. Run uses the embedded
// terminal like any script; Open in terminal hands the command line to
// Omarchy's floating terminal, where menus and full-screen programs work.
Item {
  id: pane
  property var command: null            // an entry from `runbook omarchy`, or null
  property string emptyText: "Select a command"
  property color foreground: Color.foreground
  property color accent: Color.accent
  property color urgent: Color.urgent
  property color dim: Qt.darker(foreground, 1.55)
  property string fontFamily: Style.font.family
  property string monoFamily: Style.fontFamily

  readonly property bool argsFocused: argsField.activeFocus
  readonly property string argsText: argsField.text.trim()
  // "<name>" in the usage line marks an argument the command cannot do without.
  readonly property bool needsArgs: command !== null && command.args.indexOf("<") >= 0
  readonly property string commandLine: command === null ? ""
    : command.route + (argsText !== "" ? " " + argsText : "")

  signal runRequested(string args)
  signal terminalRequested(string commandLine)
  signal addRequested(string args)

  // Only while shown: a hidden field holding focus would swallow the panel's keys.
  function focusArgs() { if (visible && command !== null && command.args !== "") argsField.forceActiveFocus() }
  function setArgs(text) { argsField.text = text }

  // Run, or ask for the arguments first when the command needs some.
  function run() {
    if (command === null) return
    if (needsArgs && argsText === "") { focusArgs(); return }
    runRequested(argsText)
  }

  // An example that starts with the route fills the arguments line with the rest.
  function exampleArgs(example) {
    if (command === null) return null
    if (example === command.route) return ""
    var prefix = command.route + " "
    return example.indexOf(prefix) === 0 ? example.substring(prefix.length) : null
  }

  component FieldLabel: Text {
    width: parent ? parent.width : 0
    textFormat: Text.PlainText
    color: pane.dim
    font.family: pane.fontFamily
    font.pixelSize: Style.font.caption
  }

  Text {
    anchors.left: parent.left
    anchors.right: parent.right
    visible: pane.command === null
    text: pane.emptyText
    textFormat: Text.PlainText
    wrapMode: Text.Wrap
    color: pane.dim
    font.family: pane.fontFamily
    font.pixelSize: Style.font.body
  }

  Flickable {
    id: flick
    anchors.fill: parent
    visible: pane.command !== null
    clip: true
    contentWidth: width
    contentHeight: column.implicitHeight
    boundsBehavior: Flickable.StopAtBounds
    interactive: contentHeight > height

    Column {
      id: column
      width: flick.width
      spacing: Style.space(10)

      Text {
        width: parent.width
        text: pane.command ? pane.command.route + (pane.command.args !== "" ? " " + pane.command.args : "") : ""
        textFormat: Text.PlainText
        wrapMode: Text.WrapAnywhere
        color: pane.foreground
        font.family: pane.monoFamily
        font.pixelSize: Style.font.body
      }

      PanelSeparator {
        width: parent.width
        foreground: pane.foreground
      }

      Text {
        width: parent.width
        text: pane.command && pane.command.summary !== "" ? pane.command.summary : "Omarchy has no description for this command."
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: pane.command && pane.command.summary !== "" ? pane.foreground : pane.dim
        font.family: pane.fontFamily
        font.pixelSize: Style.font.body
      }

      Text {
        width: parent.width
        visible: pane.command !== null && pane.command.sudo === true
        text: "Asks for your password (sudo): type it in the terminal when it asks."
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: pane.dim
        font.family: pane.fontFamily
        font.pixelSize: Style.font.bodySmall
      }

      Column {
        width: parent.width
        spacing: Style.space(4)
        visible: pane.command !== null && pane.command.examples.length > 0

        FieldLabel { text: "Examples" }

        Repeater {
          model: pane.command ? pane.command.examples : []
          delegate: Text {
            id: example
            required property string modelData
            readonly property var args: pane.exampleArgs(modelData)
            width: parent.width
            text: modelData
            textFormat: Text.PlainText
            wrapMode: Text.WrapAnywhere
            color: exampleMouse.containsMouse && args !== null ? pane.accent : pane.foreground
            font.family: pane.monoFamily
            font.pixelSize: Style.font.bodySmall

            MouseArea {
              id: exampleMouse
              anchors.fill: parent
              enabled: example.args !== null
              hoverEnabled: true
              cursorShape: example.args !== null ? Qt.PointingHandCursor : Qt.ArrowCursor
              onClicked: { pane.setArgs(example.args); pane.focusArgs() }
            }
          }
        }
      }

      Column {
        width: parent.width
        spacing: Style.space(4)
        visible: pane.command !== null && pane.command.args !== ""

        FieldLabel { text: "Arguments (shell syntax, e.g. quotes around a name with spaces)" }

        TextField {
          id: argsField
          width: parent.width
          placeholderText: pane.command ? pane.command.args : ""
          foreground: pane.foreground
          accent: pane.accent
          font.family: pane.monoFamily
          onAccepted: pane.run()
          Keys.onEscapePressed: function(event) { argsField.focus = false; event.accepted = true }
        }
      }

      Text {
        width: parent.width
        text: "Runs: " + pane.commandLine
        textFormat: Text.PlainText
        wrapMode: Text.WrapAnywhere
        color: pane.dim
        font.family: pane.monoFamily
        font.pixelSize: Style.font.bodySmall
      }

      Flow {
        width: parent.width
        spacing: Style.space(6)

        Button {
          text: "Run"
          bordered: true
          foreground: pane.accent
          fontFamily: pane.fontFamily
          tooltipText: pane.needsArgs && pane.argsText === "" ? "Type the arguments first" : "Run in the embedded terminal"
          onClicked: pane.run()
        }
        Button {
          text: "Open in terminal"
          bordered: true
          foreground: pane.foreground
          fontFamily: pane.fontFamily
          tooltipText: "Run in Omarchy's floating terminal (menus and full-screen programs work there)"
          onClicked: pane.terminalRequested(pane.commandLine)
        }
        Button {
          text: "Add to my commands"
          bordered: true
          foreground: pane.foreground
          fontFamily: pane.fontFamily
          tooltipText: "Copy into your own list, where you can edit and schedule it"
          onClicked: pane.addRequested(pane.argsText)
        }
      }

      Text {
        width: parent.width
        text: "The embedded terminal shows plain text and sends whole lines: for commands that open a menu, use Open in terminal."
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: pane.dim
        font.family: pane.fontFamily
        font.pixelSize: Style.font.caption
      }
    }
  }
}
