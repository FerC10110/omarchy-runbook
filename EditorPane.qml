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
  property var weeklyDows: ["Mon"]
  readonly property var weekdays: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
  property string customOncalendar: ""

  readonly property bool hasFocus: nameField.activeFocus || commandField.focused || helpField.focused
    || everyN.activeFocus || dailyTime.activeFocus || weeklyTime.activeFocus

  signal saveRequested(var fields)
  signal cancelRequested()

  // "Mon..Wed,Fri" -> ["Mon","Wed","Tue","Fri"] (any order); caller re-sorts.
  // Returns null when a segment is not a known day or an inverted/bad range.
  function expandDaySet(str) {
    var out = []
    var parts = str.split(",")
    for (var p = 0; p < parts.length; p++) {
      var seg = parts[p]
      var r = seg.split("..")
      if (r.length === 2) {
        var a = weekdays.indexOf(r[0]), b = weekdays.indexOf(r[1])
        if (a < 0 || b < 0 || a > b) return null
        for (var k = a; k <= b; k++) out.push(weekdays[k])
      } else {
        if (weekdays.indexOf(seg) < 0) return null
        out.push(seg)
      }
    }
    return out
  }

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
      weeklyDows = ["Mon"]
      weeklyTime.text = "08:00"
      customOncalendar = ""
    } else if (sch.kind === "interval") {
      scheduleMode = "every"
      if (sch.seconds % 86400 === 0) { everyUnit = "day"; everyN.text = String(sch.seconds / 86400) }
      else if (sch.seconds % 3600 === 0) { everyUnit = "hour"; everyN.text = String(sch.seconds / 3600) }
      else { everyUnit = "min"; everyN.text = String(Math.round(sch.seconds / 60)) }
    } else {
      var wk = /^((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:(?:,|\.\.)(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun))*) \*-\*-\* (\d{2}:\d{2}):00$/.exec(sch.oncalendar)
      var d = /^\*-\*-\* (\d{2}:\d{2}):00$/.exec(sch.oncalendar)
      if (wk) {
        var days = expandDaySet(wk[1])
        if (days === null) { scheduleMode = "custom"; customOncalendar = sch.oncalendar }
        else {
          scheduleMode = "weekly"
          weeklyDows = weekdays.filter(function(x) { return days.indexOf(x) >= 0 })
          weeklyTime.text = wk[2]
        }
      }
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
    var days = editor.weekdays.filter(function(d) { return editor.weeklyDows.indexOf(d) >= 0 })
    var expr = (scheduleMode === "weekly" ? (days.join(",") + " ") : "") + "*-*-* " + hhmm + ":00"
    return { kind: "calendar", oncalendar: expr }
  }

  function fields() {
    return { name: nameField.text, command: commandField.text, help: helpField.text,
             schedule: scheduleObject() }
  }

  // "" when the schedule (if any) is complete enough to save; a message
  // otherwise. Daily/Weekly rely on the kit TextField's acceptableInput,
  // which inputMask: "99:99" makes false for an empty or half-typed time —
  // without this gate, scheduleObject()'s "00:00" backstop would silently
  // turn an incomplete time into a real midnight schedule the user never
  // asked for, and *-*-* 00:00:00 is a valid expression the engine cannot
  // tell apart from an intentional one.
  function scheduleError() {
    if (scheduleMode === "daily" && !dailyTime.acceptableInput)
      return "Enter a complete time as HH:MM"
    if (scheduleMode === "weekly" && editor.weeklyDows.length === 0)
      return "Select at least one day"
    if (scheduleMode === "weekly" && !weeklyTime.acceptableInput)
      return "Enter a complete time as HH:MM"
    if (scheduleMode === "every" && (everyN.text === "" || parseInt(everyN.text) < 1))
      return "Enter how often (a whole number of " + everyUnit + "s)"
    return ""
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
    Flow {
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

      Flow {
        Layout.fillWidth: true
        spacing: Style.space(4)
        Repeater {
          model: editor.weekdays
          delegate: Button {
            text: modelData
            bordered: true
            active: editor.weeklyDows.indexOf(modelData) >= 0
            foreground: editor.foreground
            fontFamily: editor.fontFamily
            onClicked: {
              var s = editor.weeklyDows.slice()
              var i = s.indexOf(modelData)
              if (i >= 0) s.splice(i, 1); else s.push(modelData)
              // keep canonical order
              editor.weeklyDows = editor.weekdays.filter(function(d) { return s.indexOf(d) >= 0 })
            }
          }
        }
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
        onClicked: {
          var se = editor.scheduleError()
          if (se !== "") { editor.errorText = se; return }
          editor.saveRequested(editor.fields())
        }
      }
    }
  }
}
