import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Ui

// The embedded terminal: the tmux pane as plain monospace text, a one-line
// footer with the run state, and an input line for the odd password, Enter
// or y/n answer. Input is line based; the engine forwards it with send-keys.
Item {
  id: pane
  property var screen: ({ text: "", dead: false, exit: null, prompt: null })
  property bool alive: false
  property color foreground: Color.foreground
  property color accent: Color.accent
  property color urgent: Color.urgent
  property color dim: Qt.darker(foreground, 1.55)
  property string fontFamily: Style.font.family
  property string monoFamily: Style.fontFamily

  readonly property bool inputFocused: input.activeFocus
  // How many characters and lines fit in the viewport: the size every tmux
  // session is created and resized with.
  readonly property int cols: Math.max(20, Math.floor(viewport.width / Math.max(1, metrics.advanceWidth)))
  readonly property int rows: Math.max(5, Math.floor(viewport.height / Math.max(1, metrics.height)))
  readonly property bool passwordPrompt: screen && screen.prompt === "password"
  readonly property string footerText: alive ? "running"
    : (screen && screen.exit === 0 ? "exited 0" : "exited " + (screen && screen.exit !== null ? screen.exit : "?"))

  signal sendRequested(string text)
  signal stopRequested()
  signal closeRequested()

  function focusInput() { if (alive) input.forceActiveFocus() }

  TextMetrics {
    id: metrics
    font.family: pane.monoFamily
    font.pixelSize: Style.font.body
    text: "M"
  }

  ColumnLayout {
    anchors.fill: parent
    spacing: Style.space(6)

    Rectangle {
      Layout.fillWidth: true
      Layout.fillHeight: true
      color: Util.alpha(pane.foreground, 0.04)
      radius: Style.cornerRadius
      border.width: 1
      border.color: Util.alpha(pane.foreground, 0.12)

      Flickable {
        id: viewport
        anchors.fill: parent
        anchors.margins: Style.space(6)
        clip: true
        contentWidth: Math.max(width, screenText.implicitWidth)
        contentHeight: Math.max(height, screenText.implicitHeight)
        boundsBehavior: Flickable.StopAtBounds
        // Keep the newest lines in view when the text outgrows the viewport.
        onContentHeightChanged: contentY = Math.max(0, contentHeight - height)

        Text {
          id: screenText
          text: pane.screen ? pane.screen.text : ""
          textFormat: Text.PlainText
          wrapMode: Text.NoWrap
          color: pane.foreground
          font.family: pane.monoFamily
          font.pixelSize: Style.font.body
        }

        MouseArea {
          anchors.fill: parent
          onClicked: pane.focusInput()
        }
      }
    }

    Text {
      Layout.fillWidth: true
      text: pane.footerText
      textFormat: Text.PlainText
      color: pane.alive ? pane.accent : (pane.screen && pane.screen.exit === 0 ? pane.dim : pane.urgent)
      font.family: pane.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    RowLayout {
      Layout.fillWidth: true
      spacing: Style.space(6)

      Text {
        text: ">"
        color: pane.dim
        font.family: pane.monoFamily
        font.pixelSize: Style.font.body
      }

      TextField {
        id: input
        Layout.fillWidth: true
        enabled: pane.alive
        password: pane.passwordPrompt
        placeholderText: !pane.alive ? "Finished"
          : (pane.passwordPrompt ? "Password (sent to the terminal, never stored)" : "Type a line and press Enter")
        foreground: pane.foreground
        accent: pane.accent
        font.family: pane.monoFamily
        onAccepted: {
          var line = text
          text = ""
          pane.sendRequested(line)
        }
        Keys.onEscapePressed: function(event) { input.focus = false; event.accepted = true }
      }

      Button {
        text: "Stop"
        visible: pane.alive
        bordered: true
        foreground: pane.foreground
        fontFamily: pane.fontFamily
        tooltipText: "Interrupt (Ctrl-C, then SIGTERM, then SIGKILL); keeps the output"
        onClicked: pane.stopRequested()
      }

      Button {
        text: "Close"
        bordered: true
        foreground: pane.foreground
        fontFamily: pane.fontFamily
        tooltipText: pane.alive ? "Kill the command and discard the terminal" : "Discard the terminal"
        onClicked: pane.closeRequested()
      }
    }
  }
}
