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
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null

  // ---- state owned by this panel
  property var library: ({ version: 1, view: { width: 960, height: 540 }, scripts: [] })
  property var sessions: ({})           // id -> { dead: bool, exit: int|null }
  property string selectedId: ""
  property string mode: "help"          // "help" | "terminal" | "editor"
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
      onCloseRequested: runbook.close()
      onTabRequested: function(direction) { runbook.switchPanel(direction) }

      Text {
        anchors.centerIn: parent
        text: "Runbook · " + runbook.scripts.length + " scripts · " + runbook.runningCount + " running"
        color: runbook.foreground
        font.family: runbook.fontFamily
        font.pixelSize: Style.font.body
      }
    }
  }
}
