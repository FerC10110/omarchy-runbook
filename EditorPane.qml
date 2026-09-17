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

  // Schedule working state. "off" | "every" | "daily" | "weekly" | "custom"
  // (custom is read-only: a hand-edited OnCalendar expression that does not
  // match the Daily/Weekly patterns this form knows how to render).
  property string scheduleMode: "off"
  property string everyUnit: "min"
  property string weeklyDow: "Mon"
  property string customOncalendar: ""

  readonly property bool hasFocus: nameField.activeFocus || commandField.focused || helpField.focused
    || everyN.activeFocus || dailyTime.activeFocus || weeklyTime.activeFocus

  signal saveRequested(var fields)
  signal cancelRequested()

  function load(script) {
    nameField.text = script ? script.name : ""
    commandField.text = script ? script.command : ""
    helpField.text = script ? script.help : ""
    errorText = ""

    var sch = script ? script.schedule : null
    if (!sch) {
      // No schedule on this script: reset the mode AND the working fields,
      // so reopening the editor never shows a stale time/interval left
      // over from editing a different, scheduled script.
      scheduleMode = "off"
      everyUnit = "min"
      everyN.text = "30"
      dailyTime.text = "08:00"
      weeklyDow = "Mon"
      weeklyTime.text = "08:00"
      customOncalendar = ""
    } else if (sch.kind === "interval") {
      scheduleMode = "every"
      if (sch.seconds % 86400 === 0) { everyUnit = "day"; everyN.text = String(sch.seconds / 86400) }
      else if (sch.seconds % 3600 === 0) { everyUnit = "hour"; everyN.text = String(sch.seconds / 3600) }
      else { everyUnit = "min"; everyN.text = String(Math.round(sch.seconds / 60)) }
    } else {
      var m = /^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \*-\*-\* (\d{2}:\d{2}):00$/.exec(sch.oncalendar)
      var d = /^\*-\*-\* (\d{2}:\d{2}):00$/.exec(sch.oncalendar)
      if (m) { scheduleMode = "weekly"; weeklyDow = m[1]; weeklyTime.text = m[2] }
      else if (d) { scheduleMode = "daily"; dailyTime.text = d[1] }
      else { scheduleMode = "custom"; customOncalendar = sch.oncalendar }
    }

    Qt.callLater(function() { nameField.forceActiveFocus() })
  }

  function scheduleObject() {
    if (scheduleMode === "off") return null
    if (scheduleMode === "every") {
      var factor = everyUnit === "day" ? 86400 : (everyUnit === "hour" ? 3600 : 60)
      var n = Math.max(1, parseInt(everyN.text || "1"))
      return { kind: "interval", seconds: n * factor }
    }
    if (scheduleMode === "custom") return { kind: "calendar", oncalendar: customOncalendar }
    var t = (scheduleMode === "daily" ? dailyTime.text : weeklyTime.text) || "00:00"
    var hhmm = t.length === 5 ? t : "00:00"
    var expr = (scheduleMode === "weekly" ? (weeklyDow + " ") : "") + "*-*-* " + hhmm + ":00"
    return { kind: "calendar", oncalendar: expr }
  }

  function fields() {
    return { name: nameField.text, command: commandField.text, help: helpField.text,
             schedule: scheduleObject() }
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
    readonly property bool focused: area.activeFocus
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

    FieldLabel { text: "Schedule" }
    RowLayout {
      Layout.fillWidth: true
      spacing: Style.space(6)
      Button {
        text: "Off"
        bordered: true
        active: editor.scheduleMode === "off"
        foreground: editor.foreground
        fontFamily: editor.fontFamily
        onClicked: editor.scheduleMode = "off"
      }
      Button {
        text: "Every N"
        bordered: true
        active: editor.scheduleMode === "every"
        foreground: editor.foreground
        fontFamily: editor.fontFamily
        onClicked: editor.scheduleMode = "every"
      }
      Button {
        text: "Daily at"
        bordered: true
        active: editor.scheduleMode === "daily"
        foreground: editor.foreground
        fontFamily: editor.fontFamily
        onClicked: editor.scheduleMode = "daily"
      }
      Button {
        text: "Weekly on"
        bordered: true
        active: editor.scheduleMode === "weekly"
        foreground: editor.foreground
        fontFamily: editor.fontFamily
        onClicked: editor.scheduleMode = "weekly"
      }
      Item { Layout.fillWidth: true }
    }

    RowLayout {
      Layout.fillWidth: true
      spacing: Style.space(6)
      visible: editor.scheduleMode === "every"

      TextField {
        id: everyN
        Layout.preferredWidth: Style.space(56)
        text: "30"
        foreground: editor.foreground
        accent: editor.accent
        inputMethodHints: Qt.ImhDigitsOnly
        validator: IntValidator { bottom: 1; top: 9999 }
        Keys.onEscapePressed: function(event) { editor.cancelRequested(); event.accepted = true }
      }
      Button {
        text: "min"
        bordered: true
        active: editor.everyUnit === "min"
        foreground: editor.foreground
        fontFamily: editor.fontFamily
        onClicked: editor.everyUnit = "min"
      }
      Button {
        text: "hour"
        bordered: true
        active: editor.everyUnit === "hour"
        foreground: editor.foreground
        fontFamily: editor.fontFamily
        onClicked: editor.everyUnit = "hour"
      }
      Button {
        text: "day"
        bordered: true
        active: editor.everyUnit === "day"
        foreground: editor.foreground
        fontFamily: editor.fontFamily
        onClicked: editor.everyUnit = "day"
      }
      Item { Layout.fillWidth: true }
    }

    RowLayout {
      Layout.fillWidth: true
      spacing: Style.space(6)
      visible: editor.scheduleMode === "daily"

      TextField {
        id: dailyTime
        Layout.preferredWidth: Style.space(80)
        text: "08:00"
        placeholderText: "08:00"
        inputMask: "99:99"
        foreground: editor.foreground
        accent: editor.accent
        Keys.onEscapePressed: function(event) { editor.cancelRequested(); event.accepted = true }
      }
      Item { Layout.fillWidth: true }
    }

    ColumnLayout {
      Layout.fillWidth: true
      spacing: Style.space(6)
      visible: editor.scheduleMode === "weekly"

      RowLayout {
        Layout.fillWidth: true
        spacing: Style.space(4)
        Repeater {
          model: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
          delegate: Button {
            text: modelData
            bordered: true
            active: editor.weeklyDow === modelData
            foreground: editor.foreground
            fontFamily: editor.fontFamily
            onClicked: editor.weeklyDow = modelData
          }
        }
        Item { Layout.fillWidth: true }
      }
      TextField {
        id: weeklyTime
        Layout.preferredWidth: Style.space(80)
        text: "08:00"
        placeholderText: "08:00"
        inputMask: "99:99"
        foreground: editor.foreground
        accent: editor.accent
        Keys.onEscapePressed: function(event) { editor.cancelRequested(); event.accepted = true }
      }
    }

    Text {
      Layout.fillWidth: true
      visible: editor.scheduleMode === "custom"
      text: editor.customOncalendar
      textFormat: Text.PlainText
      wrapMode: Text.Wrap
      color: editor.dim
      font.family: editor.monoFamily
      font.pixelSize: Style.font.bodySmall
    }

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
