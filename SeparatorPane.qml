import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Ui

// Add / edit form for a separator: a label, which may stay empty for a
// plain line, and its tab. The preview draws it the way the list will.
Item {
  id: form
  property string title: "New separator"
  property string errorText: ""
  property color foreground: Color.foreground
  property color accent: Color.accent
  property color urgent: Color.urgent
  property color dim: Qt.darker(foreground, 1.55)
  property string fontFamily: Style.font.family

  // The tabs to choose from, and the one this separator goes in.
  property var tabs: []
  property string tabId: "main"
  // Editing a separator that is already in the list: the arrows move it there.
  property bool canMove: false

  signal saveRequested(var fields)
  signal cancelRequested()
  signal moveRequested(int delta)

  // defaultTab: where a new separator goes unless the form changes it.
  function load(separator, defaultTab) {
    labelField.text = separator ? separator.label : ""
    tabId = separator && separator.tab ? separator.tab : (defaultTab || "main")
    errorText = ""
    Qt.callLater(function() { labelField.forceActiveFocus() })
  }

  function save() { form.saveRequested({ kind: "separator", label: labelField.text, tab: tabId }) }

  ColumnLayout {
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: parent.top
    spacing: Style.space(6)

    Text {
      Layout.fillWidth: true
      text: form.title
      textFormat: Text.PlainText
      color: form.foreground
      font.family: form.fontFamily
      font.pixelSize: Style.font.body
      font.bold: true
    }

    Text {
      Layout.fillWidth: true
      text: "Label (optional: leave it empty for a plain line)"
      textFormat: Text.PlainText
      color: form.dim
      font.family: form.fontFamily
      font.pixelSize: Style.font.caption
    }

    TextField {
      id: labelField
      Layout.fillWidth: true
      placeholderText: "docker"
      foreground: form.foreground
      accent: form.accent
      onAccepted: form.save()
      Keys.onEscapePressed: function(event) { form.cancelRequested(); event.accepted = true }
    }

    // Where it sits: its tab, applied on Save, and its place in the list,
    // which the arrows change right away.
    Text {
      Layout.fillWidth: true
      visible: placeRow.visible
      text: tabPicker.visible ? "Tab and position" : "Position"
      textFormat: Text.PlainText
      color: form.dim
      font.family: form.fontFamily
      font.pixelSize: Style.font.caption
    }

    RowLayout {
      id: placeRow
      Layout.fillWidth: true
      visible: tabPicker.visible || form.canMove
      spacing: Style.space(6)
      Dropdown {
        id: tabPicker
        Layout.fillWidth: true
        visible: form.tabs.length > 1
        showLabel: false
        options: form.tabs.map(function(t) { return { value: t.id, label: t.name } })
        value: form.tabId
        foreground: form.foreground
        accent: form.accent
        fontFamily: form.fontFamily
        onChanged: function(value) {
          form.tabId = value
          // A pick writes the kit's value, which breaks the binding: put it back.
          tabPicker.value = Qt.binding(function() { return form.tabId })
        }
      }
      Item { Layout.fillWidth: true; visible: !tabPicker.visible }
      Button {
        text: "↑"
        visible: form.canMove
        bordered: true
        foreground: form.foreground
        fontFamily: form.fontFamily
        tooltipText: "Move up in the list"
        onClicked: form.moveRequested(-1)
      }
      Button {
        text: "↓"
        visible: form.canMove
        bordered: true
        foreground: form.foreground
        fontFamily: form.fontFamily
        tooltipText: "Move down in the list"
        onClicked: form.moveRequested(1)
      }
    }

    Text {
      Layout.fillWidth: true
      Layout.topMargin: Style.space(6)
      text: "Preview"
      textFormat: Text.PlainText
      color: form.dim
      font.family: form.fontFamily
      font.pixelSize: Style.font.caption
    }

    SeparatorLine {
      Layout.fillWidth: true
      Layout.preferredHeight: Style.space(30)
      label: labelField.text.trim()
      color: form.dim
      fontFamily: form.fontFamily
    }

    Text {
      Layout.fillWidth: true
      visible: form.errorText !== ""
      text: form.errorText
      textFormat: Text.PlainText
      wrapMode: Text.Wrap
      color: form.urgent
      font.family: form.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    RowLayout {
      Layout.fillWidth: true
      spacing: Style.space(6)
      Item { Layout.fillWidth: true }
      Button {
        text: "Cancel"
        bordered: true
        foreground: form.foreground
        fontFamily: form.fontFamily
        onClicked: form.cancelRequested()
      }
      Button {
        text: "Save"
        bordered: true
        foreground: form.accent
        fontFamily: form.fontFamily
        onClicked: form.save()
      }
    }
  }
}
