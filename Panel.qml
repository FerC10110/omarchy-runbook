import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Runbook panel: a thin face over bin/runbook. Everything it knows comes from
// the engine's JSON (the library on disk and the tmux sessions), so a script
// added by hand or a command still running after a shell restart shows up
// without anyone being told.
Panel {
  id: runbook   // not `root`: inside a Component handed to PanelHero, `root` resolves to the hero
  moduleName: "io.github.ferc10110.runbook"
  ipcTarget: "io.github.ferc10110.runbook"
  manageIpc: true

  property var anchorItem: null
  property var hostWidget: null

  // ---- state owned by this panel
  property var library: ({ version: 1, view: { width: 960, height: 540 }, scripts: [] })
  property var sessions: ({})           // id -> { dead: bool, exit: int|null }
  property var nextRuns: ({})           // id -> { next: "in 2h 5m" } for scheduled scripts with an active timer
  property string selectedId: ""
  property bool editorOpen: false
  property bool inputFocused: terminalPane.inputFocused
  // The input field disables itself (or drops focus on Escape) without
  // handing focus to anything else, which would leave PanelKeyCatcher
  // (and its j/k/x/Esc/Tab handling) unreachable until the next click.
  onInputFocusedChanged: if (!inputFocused && opened && !editorOpen) keyCatcher.forceActiveFocus()
  property int termCols: terminalPane.cols
  property int termRows: terminalPane.rows
  readonly property string mode: editorOpen ? "editor"
    : (selected !== null && sessionOf(selected.id) !== null ? "terminal" : "help")
  property string editingId: ""         // "" while adding, id while editing
  property string notice: ""
  property bool engineWriting: false    // true between a write call and its reload

  property int viewWidth: 960             // live panel size; saved through set-view
  property int viewHeight: 540

  readonly property var scripts: Array.isArray(library.scripts) ? library.scripts : []
  readonly property var selected: {
    for (var i = 0; i < scripts.length; i++) if (scripts[i].id === selectedId) return scripts[i]
    return null
  }
  readonly property int runningCount: {
    var n = 0
    for (var id in sessions) if (sessions[id] && sessions[id].dead === false) n++
    return n
  }
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  // Bar.qml does not expose an `accent` property (only foreground/urgent do);
  // guard the read so this falls back cleanly instead of assigning undefined.
  readonly property color accent: (bar && bar.accent !== undefined) ? bar.accent : Color.accent
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property string monoFamily: Style.fontFamily

  function pluginPath(relative) {
    var url = String(Qt.resolvedUrl(relative))
    return url.indexOf("file://") === 0 ? decodeURIComponent(url.substring(7)) : url
  }

  function sessionOf(id) { return sessions[id] || null }
  function isAlive(id) { var s = sessionOf(id); return s !== null && s.dead === false }

  function selectScript(id) {
    if (editorOpen) return
    selectedId = id
    editorOpen = false
    if (isAlive(id)) Qt.callLater(terminalPane.focusInput)
  }

  function moveSelection(delta) {
    if (scripts.length === 0) return
    var at = -1
    for (var i = 0; i < scripts.length; i++) if (scripts[i].id === selectedId) { at = i; break }
    at = Math.max(0, Math.min(scripts.length - 1, at + delta))
    selectedId = scripts[at].id
  }

  // sessions is replaced (not mutated) so every binding on it re-evaluates.
  function markSession(id, dead, exit) {
    var patch = {}
    patch[id] = { dead: dead, exit: exit }
    sessions = Object.assign({}, sessions, patch)
  }

  function dropSession(id) {
    if (!sessions[id]) return
    var next = Object.assign({}, sessions)
    delete next[id]
    sessions = next
  }

  // ▶: start a session when there is none or it is dead; only select when it
  // is alive (the terminal comes into view because mode turns "terminal").
  function playScript(id) {
    if (editorOpen) return
    selectScript(id)
    if (isAlive(id)) return
    engineCall(["run", id, "--cols", String(termCols), "--rows", String(termRows)], null, function(payload) {
      if (!payload || payload.error !== undefined) return
      screenEpoch++
      markSession(id, false, null)
      refreshStatus()
    })
  }

  // ---- terminal screen: polled every 300 ms while the panel is open and the
  // selected script has a session. A dead session is fetched once more and
  // then left alone until it is run again.
  function emptyScreen() { return { text: "", dead: false, exit: null, prompt: null } }

  property var screen: emptyScreen()
  property string screenFor: ""
  // Bumped by every action that changes session state (run/stop/close) so a
  // screenProc reply started before that action, and landing after it, is
  // recognised as stale and dropped instead of reviving what the action just
  // changed. See applyScreen().
  property int screenEpoch: 0
  readonly property bool viewingTerminal: opened && mode === "terminal"
  readonly property bool screenSettled: screenFor === selectedId && screen.dead === true && !isAlive(selectedId)

  function pollScreen() {
    if (screenProc.running || selectedId === "" || sessionOf(selectedId) === null) return
    screenProc.targetId = selectedId
    screenProc.epoch = screenEpoch
    screenProc.command = [pluginPath("bin/runbook"), "screen", selectedId]
    screenProc.running = true
  }

  function applyScreen(id, epoch, text) {
    var payload = null
    try { payload = JSON.parse(String(text || "")) } catch (e) { return }
    // A stop/close/run landed while this poll was in flight: its answer no
    // longer reflects the current session state, on either the error or the
    // success path, so ignore it outright.
    if (epoch !== screenEpoch) return
    if (!payload) return
    if (payload.error !== undefined) {
      // "No session for this script": forget it so the pane goes back to help.
      dropSession(id)
      if (screenFor === id) screenFor = ""
      return
    }
    if (id !== selectedId) return
    var next = {
      text: String(payload.text || ""),
      dead: payload.dead === true,
      exit: payload.exit === undefined ? null : payload.exit,
      prompt: payload.prompt || null
    }
    // A session a close already dropped must not be repopulated by a poll
    // that was merely late, not stale by epoch (e.g. one issued for `id`
    // just before it stopped being the selected script's session at all).
    var known = sessionOf(id)
    if (known === null) return
    screenFor = id
    // Replace only on change, so the Text under the pointer is not rebuilt.
    if (screen.text !== next.text || screen.dead !== next.dead || screen.exit !== next.exit || screen.prompt !== next.prompt)
      screen = next
    if (known.dead !== next.dead || known.exit !== next.exit) markSession(id, next.dead, next.exit)
  }

  Process {
    id: screenProc
    property string targetId: ""
    property int epoch: 0
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: runbook.applyScreen(screenProc.targetId, screenProc.epoch, text)
    }
  }

  Timer {
    interval: 300
    running: runbook.viewingTerminal && !runbook.screenSettled
    repeat: true
    triggeredOnStart: true
    onTriggered: runbook.pollScreen()
  }

  onSelectedIdChanged: {
    // A different script: show nothing stale while its first screen arrives.
    if (screenFor !== selectedId) screen = emptyScreen()
    // The input line is per-selection: never carry typed text to another script.
    terminalPane.clearInput()
  }

  function sendLine(id, text) {
    engineCall(["send", id], { text: text }, function(payload) { pollScreen() })
  }

  function stopScript(id) {
    engineCall(["stop", id], null, function(payload) {
      if (!payload || payload.error !== undefined) return
      screenEpoch++
      markSession(id, payload.dead === true, payload.exit === undefined ? null : payload.exit)
      pollScreen()
    })
  }

  function closeScript(id) {
    engineCall(["close", id], null, function(payload) {
      // Unconditional, unlike stop/play/applyScreen: a close on a session
      // that is already gone (engine error) must still clear the pane.
      screenEpoch++
      dropSession(id)
      if (screenFor === id) { screenFor = ""; screen = emptyScreen() }
      terminalPane.clearInput()
    })
  }

  // ---- add / edit / delete
  function startAdd() {
    editingId = ""
    editorPane.title = "New script"
    editorPane.load(null)
    editorOpen = true
  }

  function startEdit() {
    if (selected === null) return
    editingId = selected.id
    editorPane.title = "Edit script"
    editorPane.load(selected)
    editorOpen = true
  }

  function cancelEditor() {
    editorOpen = false
    keyCatcher.forceActiveFocus()
  }

  function saveEditor(fields) {
    var args = editingId === "" ? ["add"] : ["update", editingId]
    var adding = editingId === ""
    engineWriting = true
    engineCall(args, fields, function(payload) {
      engineWriting = false
      if (!payload || payload.error !== undefined) {
        editorPane.errorText = payload && payload.error ? payload.error : "The engine did not answer"
        return
      }
      applyLibrary(payload)
      if (adding && scripts.length > 0) selectedId = scripts[scripts.length - 1].id
      editorOpen = false
      keyCatcher.forceActiveFocus()
    })
  }

  function requestDelete() {
    if (selected === null) return
    confirm.message = "Delete \"" + selected.name + "\"?"
      + (sessionOf(selected.id) !== null ? " Its terminal will be closed." : "")
    confirm.selectedIndex = 0
    confirm.opened = true
    keyCatcher.forceActiveFocus()
  }

  function performDelete() {
    var id = selectedId
    confirm.opened = false
    if (id === "") return
    engineWriting = true
    engineCall(["remove", id], null, function(payload) {
      engineWriting = false
      if (!payload || payload.error !== undefined) return
      dropSession(id)
      if (screenFor === id) { screenFor = ""; screen = emptyScreen() }
      selectedId = ""
      applyLibrary(payload)
    })
  }

  // ---- panel size: dragged live, saved on release, sessions resized to fit
  function setViewSize(w, h) {
    var maxW = Math.min(4000, panel.availableCardWidth > 0 ? panel.availableCardWidth : 4000)
    var maxH = Math.min(3000, panel.availableCardHeight > 0 ? panel.availableCardHeight : 3000)
    viewWidth = Math.round(Math.max(600, Math.min(maxW, w)))
    viewHeight = Math.round(Math.max(360, Math.min(maxH, h)))
  }

  function commitView() {
    if (!library.view || library.view.width !== viewWidth || library.view.height !== viewHeight) {
      engineWriting = true
      engineCall(["set-view"], { width: viewWidth, height: viewHeight }, function(payload) {
        engineWriting = false
        if (payload && payload.error === undefined) library = payload
      })
    }
    if (selectedId !== "" && sessionOf(selectedId) !== null)
      engineCall(["resize", selectedId, "--cols", String(termCols), "--rows", String(termRows)], null, function(payload) { pollScreen() })
  }

  // Clipboard via wl-copy with the text as its argument: no shell, no quoting.
  function copyText(text) {
    Quickshell.execDetached(["wl-copy", "--", String(text)])
    showNotice("Copied")
  }

  function showNotice(text) {
    notice = String(text || "")
    noticeTimer.restart()
  }

  Timer { id: noticeTimer; interval: 5000; onTriggered: runbook.notice = "" }

  // ---- engine: one Process for read/write calls, one call in flight, a queue
  // of pending calls. Screen polling gets its own Process in Task 9 so a slow
  // write never stalls the terminal.
  property var engineQueue: []
  property var engineCurrent: null

  function engineCall(args, stdinJson, onDone) {
    engineQueue.push({ args: args, stdin: stdinJson, onDone: onDone })
    engineQueue = engineQueue
    pumpEngine()
  }

  function pumpEngine() {
    if (engineCurrent !== null || engineQueue.length === 0) return
    engineCurrent = engineQueue.shift()
    engineQueue = engineQueue
    engineProc.command = [pluginPath("bin/runbook")].concat(engineCurrent.args)
    engineProc.running = true
  }

  function finishEngine(text) {
    var call = engineCurrent
    engineCurrent = null
    var payload = null
    try { payload = JSON.parse(String(text || "")) } catch (e) { payload = { error: "Engine returned no JSON" } }
    if (payload && payload.error !== undefined) showNotice(payload.error)
    if (call && call.onDone) call.onDone(payload)
    pumpEngine()
  }

  Process {
    id: engineProc
    stdinEnabled: true
    onStarted: {
      var call = runbook.engineCurrent
      // The engine reads exactly one line, so the pipe can stay open.
      if (call && call.stdin !== null && call.stdin !== undefined) write(JSON.stringify(call.stdin) + "\n")
    }
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: runbook.finishEngine(text)
    }
    stderr: StdioCollector { waitForEnd: true }
  }

  function applyLibrary(payload) {
    if (!payload || payload.error !== undefined) return
    library = payload
    if (payload.view && payload.view.width > 0 && payload.view.height > 0) {
      viewWidth = payload.view.width
      viewHeight = payload.view.height
    }
    if (selected === null && scripts.length > 0) selectedId = scripts[0].id
    if (scripts.length === 0) selectedId = ""
    refreshSchedules()
  }

  function refreshSchedules() {
    engineCall(["schedules"], null, function(p) {
      if (p && p.error === undefined) nextRuns = p
    })
  }

  readonly property var _weekdays: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
  readonly property var _dowNames: ({ Mon: "Mondays", Tue: "Tuesdays", Wed: "Wednesdays",
    Thu: "Thursdays", Fri: "Fridays", Sat: "Saturdays", Sun: "Sundays" })

  // "Mon..Wed,Fri" -> ["Mon","Wed","Tue","Fri"] (any order); caller re-sorts.
  // Returns null when a segment is not a known day or an inverted/bad range.
  function _expandDaySet(str) {
    var out = []
    var parts = str.split(",")
    for (var p = 0; p < parts.length; p++) {
      var seg = parts[p]
      var r = seg.split("..")
      if (r.length === 2) {
        var a = _weekdays.indexOf(r[0]), b = _weekdays.indexOf(r[1])
        if (a < 0 || b < 0 || a > b) return null
        for (var k = a; k <= b; k++) out.push(_weekdays[k])
      } else {
        if (_weekdays.indexOf(seg) < 0) return null
        out.push(seg)
      }
    }
    return out
  }

  function describeSchedule(s) {
    if (!s) return ""
    if (s.kind === "interval") {
      var sec = s.seconds, n, unit
      if (sec % 86400 === 0) { n = sec / 86400; unit = "day" }
      else if (sec % 3600 === 0) { n = sec / 3600; unit = "hour" }
      else { n = Math.round(sec / 60); unit = "minute" }
      return "Runs every " + n + " " + unit + (n === 1 ? "" : "s")
    }
    var wk = /^((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:(?:,|\.\.)(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun))*) \*-\*-\* (\d{2}:\d{2}):00$/.exec(s.oncalendar)
    var dy = /^\*-\*-\* (\d{2}:\d{2}):00$/.exec(s.oncalendar)
    if (wk) {
      var days = _expandDaySet(wk[1])
      if (days) {
        days = _weekdays.filter(function(x) { return days.indexOf(x) >= 0 }).map(function(x) { return _dowNames[x] })
        var list = days.length === 1 ? days[0]
          : days.slice(0, -1).join(", ") + " and " + days[days.length - 1]
        return "Runs on " + list + " at " + wk[2]
      }
    }
    if (dy) return "Runs daily at " + dy[1]
    return "Runs on schedule: " + s.oncalendar
  }

  function refreshList() {
    engineCall(["list"], null, applyLibrary)
  }

  function refreshStatus() {
    if (statusProc.running) return
    statusProc.running = true
  }

  // Status has its own Process so the icon keeps updating while a write is queued.
  Process {
    id: statusProc
    command: [runbook.pluginPath("bin/runbook"), "status"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var payload = null
        try { payload = JSON.parse(String(text || "")) } catch (e) { return }
        if (payload && payload.sessions !== undefined) runbook.sessions = payload.sessions
      }
    }
  }

  Timer {
    interval: runbook.opened ? 2000 : 5000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: runbook.refreshStatus()
  }

  // scripts.json edited by hand (or by the engine) → reload, unless we are the
  // ones writing right now (the call's own reply carries the new library).
  FileView {
    id: libraryWatch
    path: (Quickshell.env("XDG_CONFIG_HOME") || (Quickshell.env("HOME") + "/.config")) + "/runbook/scripts.json"
    watchChanges: true
    printErrors: false
    onFileChanged: if (!runbook.engineWriting) runbook.refreshList()
    onLoadFailed: {}
  }

  onOpenedChanged: {
    if (opened) {
      refreshList()
      refreshStatus()
    } else {
      confirm.opened = false
      editorOpen = false
    }
  }

  Component.onCompleted: refreshList()

  KeyboardPanel {
    id: panel
    anchorItem: runbook.anchorItem
    owner: runbook.hostWidget || runbook
    bar: runbook.bar
    open: runbook.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(runbook.viewWidth)
    contentHeight: panel.cappedContentHeight(runbook.viewHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      // While a text field owns the keyboard, letters must reach it.
      blocked: (runbook.editorOpen || runbook.inputFocused) && !confirm.opened
      onCloseRequested: {
        if (confirm.opened) confirm.opened = false
        else if (runbook.editorOpen) runbook.cancelEditor()
        else runbook.close()
      }
      onTabRequested: function(direction) {
        if (confirm.opened) confirm.selectedIndex = confirm.selectedIndex === 0 ? 1 : 0
        else runbook.switchPanel(direction)
      }
      onMoveRequested: function(dx, dy) {
        if (confirm.opened) { if (dx !== 0) confirm.selectedIndex = confirm.selectedIndex === 0 ? 1 : 0; return }
        if (dy !== 0) runbook.moveSelection(dy)
      }
      onActivateRequested: {
        // Enter only answers the dialog; it never runs a script.
        if (!confirm.opened) return
        if (confirm.selectedIndex === 0) confirm.opened = false
        else runbook.performDelete()
      }
      onDeleteRequested: { if (!confirm.opened && runbook.mode !== "editor") runbook.requestDelete() }
      onTextKey: function(text) {
        if (text === "c" && runbook.mode === "terminal" && !confirm.opened) terminalPane.copyOutput()
      }

      RowLayout {
        anchors.fill: parent
        spacing: Style.space(12)

        // ---------------------------------------------------- left column
        ColumnLayout {
          id: leftColumn
          Layout.preferredWidth: Style.space(260)
          Layout.minimumWidth: Style.space(260)
          Layout.maximumWidth: Style.space(260)
          Layout.fillHeight: true
          spacing: Style.space(8)

          PanelHero {
            Layout.fillWidth: true
            title: "Runbook"
            meta: runbook.scripts.length === 1 ? "1 script" : runbook.scripts.length + " scripts"
            foreground: runbook.foreground
            fontFamily: runbook.fontFamily

            trailingControl: Component {
              PanelActionButton {
                iconText: "+"
                tooltipText: "Add a command"
                enabled: runbook.mode !== "editor"
                foreground: runbook.foreground
                hoverColor: runbook.accent
                onClicked: runbook.startAdd()
              }
            }
          }

          Text {
            Layout.fillWidth: true
            visible: runbook.notice !== ""
            text: runbook.notice
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: runbook.urgent
            font.family: runbook.fontFamily
            font.pixelSize: Style.font.bodySmall
          }

          ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: Style.space(2)
            boundsBehavior: Flickable.StopAtBounds
            model: runbook.scripts
            delegate: ScriptRow {
              selected: runbook.selectedId === modelData.id
              session: runbook.sessionOf(modelData.id)
              foreground: runbook.foreground
              accent: runbook.accent
              urgent: runbook.urgent
              fontFamily: runbook.fontFamily
              onSelect: runbook.selectScript(modelData.id)
              onPlay: runbook.playScript(modelData.id)
            }
          }

          RowLayout {
            Layout.fillWidth: true
            spacing: Style.space(6)
            Button {
              text: "Edit"
              bordered: true
              enabled: runbook.selected !== null && runbook.mode !== "editor"
              foreground: runbook.foreground
              fontFamily: runbook.fontFamily
              onClicked: runbook.startEdit()
            }
            Button {
              text: "Delete"
              bordered: true
              enabled: runbook.selected !== null && runbook.mode !== "editor"
              foreground: runbook.urgent
              fontFamily: runbook.fontFamily
              onClicked: runbook.requestDelete()
            }
            Item { Layout.fillWidth: true }
          }
        }

        Rectangle {
          Layout.fillHeight: true
          width: 1
          color: Util.alpha(runbook.foreground, 0.12)
        }

        // --------------------------------------------------- right column
        Item {
          id: rightColumn
          Layout.fillWidth: true
          Layout.fillHeight: true

          // Help view: the command in monospace, a separator, the help text.
          Flickable {
            id: helpPane
            anchors.fill: parent
            visible: runbook.mode === "help"
            clip: true
            contentWidth: width
            contentHeight: helpColumn.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            interactive: contentHeight > height

            Column {
              id: helpColumn
              width: helpPane.width
              spacing: Style.space(10)

              Text {
                width: parent.width
                visible: runbook.selected === null
                text: runbook.scripts.length === 0 ? "Add a command with +" : "Select a script"
                textFormat: Text.PlainText
                color: runbook.dim
                font.family: runbook.fontFamily
                font.pixelSize: Style.font.body
              }

              Text {
                width: parent.width
                visible: runbook.selected !== null
                text: runbook.selected ? runbook.selected.command : ""
                textFormat: Text.PlainText
                wrapMode: Text.WrapAnywhere
                color: runbook.foreground
                font.family: runbook.monoFamily
                font.pixelSize: Style.font.body
              }

              PanelSeparator {
                width: parent.width
                visible: runbook.selected !== null
                foreground: runbook.foreground
              }

              Text {
                width: parent.width
                visible: runbook.selected !== null
                text: runbook.selected && runbook.selected.help !== "" ? runbook.selected.help : "No help text yet. Use Edit to add one."
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                color: runbook.selected && runbook.selected.help !== "" ? runbook.foreground : runbook.dim
                font.family: runbook.fontFamily
                font.pixelSize: Style.font.body
              }

              Text {
                width: parent.width
                visible: runbook.selected !== null && runbook.selected.schedule ? true : false
                text: {
                  if (!runbook.selected || !runbook.selected.schedule) return ""
                  var base = runbook.describeSchedule(runbook.selected.schedule)
                  var nr = runbook.nextRuns[runbook.selected.id]
                  return nr && nr.next ? base + " · next " + nr.next : base
                }
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                color: runbook.dim
                font.family: runbook.fontFamily
                font.pixelSize: Style.font.bodySmall
              }
            }
          }

          TerminalPane {
            id: terminalPane
            anchors.fill: parent
            visible: runbook.mode === "terminal"
            screen: runbook.screen
            alive: runbook.isAlive(runbook.selectedId)
            foreground: runbook.foreground
            accent: runbook.accent
            urgent: runbook.urgent
            dim: runbook.dim
            fontFamily: runbook.fontFamily
            monoFamily: runbook.monoFamily
            onSendRequested: function(text) { runbook.sendLine(runbook.selectedId, text) }
            onStopRequested: runbook.stopScript(runbook.selectedId)
            onCloseRequested: runbook.closeScript(runbook.selectedId)
            onCopyRequested: function(text) { runbook.copyText(text) }
          }

          EditorPane {
            id: editorPane
            anchors.fill: parent
            visible: runbook.mode === "editor"
            foreground: runbook.foreground
            accent: runbook.accent
            urgent: runbook.urgent
            dim: runbook.dim
            fontFamily: runbook.fontFamily
            monoFamily: runbook.monoFamily
            onSaveRequested: function(fields) { runbook.saveEditor(fields) }
            onCancelRequested: runbook.cancelEditor()
          }
        }
      }

      ConfirmDialog {
        id: confirm
        anchors.fill: parent
        z: 10
        cancelText: "Cancel"
        confirmText: "Delete"
        background: Color.popups.background
        foreground: runbook.foreground
        selectedText: runbook.accent
        fontFamily: runbook.fontFamily
        onCanceled: confirm.opened = false
        onConfirmed: runbook.performDelete()
      }

      // Bottom-right grip: drag to resize. Sits in the card's own padding
      // (negative margins push it past the content edge into that band) so
      // it never overlaps a content-area button like Save or Close. Scene
      // coordinates for the drag math, because the panel can shift while it
      // grows (same trick as the camera plugin).
      MouseArea {
        id: grip
        width: panel.padding + Style.space(4)
        height: panel.padding + Style.space(4)
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.rightMargin: -(panel.padding + 2)
        anchors.bottomMargin: -(panel.padding + 2)
        z: 5
        hoverEnabled: true
        preventStealing: true
        cursorShape: Qt.SizeFDiagCursor
        acceptedButtons: Qt.LeftButton
        property point dragStart
        property int startWidth: 0
        property int startHeight: 0
        onPressed: function(mouse) {
          dragStart = mapToItem(null, mouse.x, mouse.y)
          startWidth = runbook.viewWidth
          startHeight = runbook.viewHeight
        }
        onPositionChanged: function(mouse) {
          if (!pressed) return
          var point = mapToItem(null, mouse.x, mouse.y)
          runbook.setViewSize(startWidth + (point.x - dragStart.x), startHeight + (point.y - dragStart.y))
        }
        onReleased: runbook.commitView()
        Text {
          anchors.right: parent.right
          anchors.bottom: parent.bottom
          text: "◢"
          color: grip.containsMouse || grip.pressed ? runbook.accent : runbook.dim
          font.pixelSize: Style.font.caption
        }
      }
    }
  }


}
