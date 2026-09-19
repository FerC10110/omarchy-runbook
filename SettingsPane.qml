import QtQuick
import QtQuick.Layouts
import Quickshell
import qs.Commons
import qs.Ui

// Settings: the tabs (add, rename, reorder, delete), the Omarchy tab on or
// off, and the Readily integration with the tab its commands go in. Every
// change is a request to the panel, which sends it to the engine; nothing
// here holds state of its own beyond the text being typed.
//
// A FocusScope so it can own Escape the way each EditorPane field owns it:
// the kit Toggle only wires Return/Enter/Space, so an Escape pressed while it
// has focus bubbles up to here.
FocusScope {
  id: settings
  property var config: ({})
  property var tabs: []
  property color foreground: Color.foreground
  property color accent: Color.accent
  property color urgent: Color.urgent
  property color dim: Qt.darker(foreground, 1.55)
  property string fontFamily: Style.font.family

  readonly property bool readilyEnabled: !!(config && config.readily && config.readily.enabled)
  readonly property bool omarchyEnabled: !(config && config.omarchy && config.omarchy.enabled === false)
  readonly property string mainTabName: {
    for (var i = 0; i < tabs.length; i++) if (tabs[i].id === "main") return tabs[i].name
    return "Mine"
  }

  signal closeRequested()
  signal configRequested(var patch)
  signal addTabRequested(string name)
  signal renameTabRequested(string id, string name)
  signal moveTabRequested(string id, int delta)
  signal removeTabRequested(string id)

  Keys.onEscapePressed: function(event) { settings.closeRequested(); event.accepted = true }

  function addTab() {
    var name = newTab.text.trim()
    if (name === "") return
    settings.addTabRequested(name)
    newTab.text = ""
  }

  Flickable {
    id: flick
    anchors.fill: parent
    clip: true
    contentWidth: width
    contentHeight: column.implicitHeight
    boundsBehavior: Flickable.StopAtBounds
    interactive: contentHeight > height

    Column {
      id: column
      width: flick.width
      spacing: Style.space(10)

      PanelSectionHeader {
        text: "Tabs"
        foreground: settings.foreground
        fontFamily: settings.fontFamily
      }

      Repeater {
        model: settings.tabs
        delegate: RowLayout {
          id: tabRow
          required property var modelData
          required property int index
          width: column.width
          spacing: Style.space(6)

          // Renames when the field is left or Enter is pressed.
          TextField {
            Layout.fillWidth: true
            text: tabRow.modelData.name
            foreground: settings.foreground
            accent: settings.accent
            onEditingFinished: {
              var name = text.trim()
              if (name !== "" && name !== tabRow.modelData.name) settings.renameTabRequested(tabRow.modelData.id, name)
              else text = tabRow.modelData.name
            }
            Keys.onEscapePressed: function(event) { text = tabRow.modelData.name; settings.forceActiveFocus(); event.accepted = true }
          }
          Button {
            text: "↑"
            bordered: true
            enabled: tabRow.index > 0
            foreground: settings.foreground
            fontFamily: settings.fontFamily
            tooltipText: "Earlier in the tab bar"
            onClicked: settings.moveTabRequested(tabRow.modelData.id, -1)
          }
          Button {
            text: "↓"
            bordered: true
            enabled: tabRow.index < settings.tabs.length - 1
            foreground: settings.foreground
            fontFamily: settings.fontFamily
            tooltipText: "Later in the tab bar"
            onClicked: settings.moveTabRequested(tabRow.modelData.id, 1)
          }
          Button {
            text: "Delete"
            bordered: true
            // The main tab takes the commands of a deleted one, so it stays.
            enabled: tabRow.modelData.id !== "main"
            opacity: enabled ? 1.0 : 0.35
            foreground: settings.urgent
            fontFamily: settings.fontFamily
            tooltipText: enabled ? "Delete this tab; its commands move to \"" + settings.mainTabName + "\""
              : "This tab takes the commands of deleted tabs: rename it instead"
            onClicked: settings.removeTabRequested(tabRow.modelData.id)
          }
        }
      }

      RowLayout {
        width: column.width
        spacing: Style.space(6)
        TextField {
          id: newTab
          Layout.fillWidth: true
          placeholderText: "New tab name, e.g. Docker"
          foreground: settings.foreground
          accent: settings.accent
          onAccepted: settings.addTab()
          Keys.onEscapePressed: function(event) { text = ""; settings.forceActiveFocus(); event.accepted = true }
        }
        Button {
          text: "Add tab"
          bordered: true
          enabled: newTab.text.trim() !== ""
          foreground: settings.accent
          fontFamily: settings.fontFamily
          onClicked: settings.addTab()
        }
      }

      Text {
        width: column.width
        text: "Choose a command's tab in its Edit form. Deleting a tab moves its commands to \""
          + settings.mainTabName + "\"."
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: settings.dim
        font.family: settings.fontFamily
        font.pixelSize: Style.font.caption
      }

      PanelSeparator {
        width: column.width
        foreground: settings.foreground
      }

      Toggle {
        width: column.width
        label: "Omarchy tab"
        description: "Show a tab with Omarchy's own commands (search them with /)"
        checked: settings.omarchyEnabled
        foreground: settings.foreground
        accent: settings.accent
        fontFamily: settings.fontFamily
        onClicked: settings.configRequested({ omarchy: { enabled: !checked } })
      }

      PanelSeparator {
        width: column.width
        foreground: settings.foreground
      }

      Toggle {
        width: column.width
        label: "Integrate with Readily"
        description: "Show #runbook-tagged commands from your Readily notes"
        checked: settings.readilyEnabled
        foreground: settings.foreground
        accent: settings.accent
        fontFamily: settings.fontFamily
        onClicked: settings.configRequested({ readily: { enabled: !checked } })
      }

      Column {
        width: column.width
        spacing: Style.space(4)
        visible: settings.readilyEnabled && settings.tabs.length > 1

        Text {
          text: "New Readily commands go in the tab"
          textFormat: Text.PlainText
          color: settings.dim
          font.family: settings.fontFamily
          font.pixelSize: Style.font.caption
        }

        Dropdown {
          id: readilyTab
          width: parent.width
          showLabel: false
          options: settings.tabs.map(function(t) { return { value: t.id, label: t.name } })
          value: settings.config && settings.config.readily ? settings.config.readily.tab : "main"
          foreground: settings.foreground
          accent: settings.accent
          fontFamily: settings.fontFamily
          onChanged: function(value) {
            // A pick writes the kit's value, which breaks the binding: put it back.
            readilyTab.value = Qt.binding(function() {
              return settings.config && settings.config.readily ? settings.config.readily.tab : "main"
            })
            settings.configRequested({ readily: { tab: value } })
          }
        }
      }

      Text {
        text: "What is Readily?"
        textFormat: Text.PlainText
        color: settings.accent
        font.family: settings.fontFamily
        font.pixelSize: Style.font.bodySmall

        MouseArea {
          anchors.fill: parent
          cursorShape: Qt.PointingHandCursor
          onClicked: Quickshell.execDetached(["xdg-open", "https://plugins.omarchy.org/plugin.html?id=io.github.ferc10110.readily"])
        }
      }
    }
  }
}
