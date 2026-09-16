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
  property string selectedId: ""
  property bool editorOpen: false
  property bool inputFocused: false     // bound to the terminal input in Task 9
  property int termCols: 80             // bound to the terminal's measured size in Task 9
  property int termRows: 24
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
    selectedId = id
    editorOpen = false
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
    selectScript(id)
    if (isAlive(id)) return
    engineCall(["run", id, "--cols", String(termCols), "--rows", String(termRows)], null, function(payload) {
      if (!payload || payload.error !== undefined) return
      markSession(id, false, null)
      refreshStatus()
    })
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
      blocked: runbook.editorOpen || runbook.inputFocused
      onCloseRequested: runbook.close()
      onTabRequested: function(direction) { runbook.switchPanel(direction) }
      onMoveRequested: function(dx, dy) { if (dy !== 0) runbook.moveSelection(dy) }
      // Enter and Space are deliberately not wired: only ▶ runs a script.

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
            }
          }
        }
      }
    }
  }
}
