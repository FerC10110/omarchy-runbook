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
  property var library: ({ version: 1, view: { width: 960, height: 540 }, tabs: [{ id: "main", name: "Mine" }], scripts: [] })
  property var sessions: ({})           // id -> { dead: bool, exit: int|null }
  property var nextRuns: ({})           // id -> { next: "in 2h 5m" } for scheduled scripts with an active timer
  property var config: ({ readily: { enabled: false, tab: "main" }, omarchy: { enabled: true } })
  property string selectedId: ""
  property bool editorOpen: false
  property bool settingsOpen: false
  property bool inputFocused: terminalPane.inputFocused || omarchySearch.activeFocus || omarchyPane.argsFocused
  // The input field disables itself (or drops focus on Escape) without
  // handing focus to anything else, which would leave PanelKeyCatcher
  // (and its j/k/x/Esc/Tab handling) unreachable until the next click.
  onInputFocusedChanged: if (!inputFocused && opened && !editorOpen) keyCatcher.forceActiveFocus()
  property int termCols: terminalPane.cols
  property int termRows: terminalPane.rows
  readonly property string mode: settingsOpen ? "settings"
    : editorOpen ? "editor"
    : (selected !== null && sessionOf(selected.id) !== null ? "terminal"
    : (view === "omarchy" ? "omarchy" : "help"))
  property string editingId: ""         // "" while adding, id while editing
  property bool editingReadily: false   // true while editing a read-only Readily row (schedule-only)
  property string editorKind: "script"  // "script" | "separator": which form the editor shows
  property string addAfter: ""          // a new item goes right below this one ("" = at the end)
  property var confirmAction: null      // what the confirm dialog's destructive button does
  property string notice: ""
  property bool engineWriting: false    // true between a write call and its reload

  property int viewWidth: 960             // live panel size; saved through set-view
  property int viewHeight: 540

  // ---- tabs: the user's (from scripts.json, "main" always among them), then
  // Omarchy's own commands when that tab is on, read through the engine from
  // `omarchy commands --json`. Each tab keeps its own selection.
  property string view: "main"            // a tab id, or "omarchy"
  property string lastUserTab: "main"     // where new items go while the Omarchy tab shows
  property var viewSelection: ({})
  property var omarchyCommands: []
  property string omarchyWarning: ""
  property bool omarchyLoading: false
  property string omarchyQuery: ""
  property var omarchyLastArgs: ({})      // id -> the arguments it last ran with

  readonly property var scripts: Array.isArray(library.scripts) ? library.scripts : []
  readonly property var tabs: Array.isArray(library.tabs) && library.tabs.length > 0
    ? library.tabs : [{ id: "main", name: "Mine" }]
  readonly property bool omarchyEnabled: !(config && config.omarchy && config.omarchy.enabled === false)
  readonly property var tabChips: omarchyEnabled ? tabs.concat([{ id: "omarchy", name: "Omarchy" }]) : tabs
  readonly property var tabScripts: scripts.filter(function(s) { return (s.tab || "main") === runbook.view })
  // Every word typed must appear in the command's route or its summary.
  // Commands whose route holds the whole query come first, then those whose
  // route holds every word, then summary-only matches ("set" is also in "reset").
  readonly property var omarchyVisible: {
    var terms = omarchyQuery.toLowerCase().split(/\s+/).filter(function(t) { return t !== "" })
    if (terms.length === 0) return omarchyCommands
    var query = terms.join(" ")
    var ranked = []
    for (var i = 0; i < omarchyCommands.length; i++) {
      var c = omarchyCommands[i]
      var name = c.name.toLowerCase()
      var text = name + " " + c.summary.toLowerCase()
      var inName = 0, inText = 0
      for (var t = 0; t < terms.length; t++) {
        if (name.indexOf(terms[t]) >= 0) inName++
        if (text.indexOf(terms[t]) >= 0) inText++
      }
      if (inText < terms.length) continue
      var rank = name.indexOf(query) >= 0 ? 0 : (inName === terms.length ? 1 : 2)
      ranked.push({ command: c, rank: rank, order: i })
    }
    ranked.sort(function(a, b) { return a.rank - b.rank || a.order - b.order })
    return ranked.map(function(r) { return r.command })
  }
  readonly property var currentList: view === "omarchy" ? omarchyVisible : tabScripts
  readonly property var selected: {
    var pool = view === "omarchy" ? omarchyCommands : scripts
    for (var i = 0; i < pool.length; i++) if (pool[i].id === selectedId) return pool[i]
    return null
  }
  // The selection in a user tab: a script, a Readily row or a separator.
  readonly property var selectedScript: view !== "omarchy" ? selected : null
  readonly property bool selectedIsSeparator: selectedScript !== null && selectedScript.kind === "separator"
  // Rows that live in scripts.json, the only ones that can be edited freely or deleted.
  // Every row of a user tab can be moved, Readily ones included.
  readonly property bool selectedIsNative: selectedScript !== null && selectedScript.source !== "readily"
  // The help pane binds to this: never an Omarchy entry or a separator (no command/help fields).
  readonly property var helpScript: selectedIsSeparator ? null : selectedScript
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
    var pool = currentList
    if (pool.length === 0) return
    var at = -1
    for (var i = 0; i < pool.length; i++) if (pool[i].id === selectedId) { at = i; break }
    at = Math.max(0, Math.min(pool.length - 1, at + delta))
    selectedId = pool[at].id
    list.positionViewAtIndex(at, ListView.Contain)
  }

  function hasTab(id) {
    for (var i = 0; i < tabChips.length; i++) if (tabChips[i].id === id) return true
    return false
  }

  // The user tab new items go in: the one showing, or the last one shown
  // while the Omarchy tab is up.
  function userTab() {
    if (view !== "omarchy") return view
    return hasTab(lastUserTab) ? lastUserTab : tabs[0].id
  }

  // Switch tabs. The selection each tab had is remembered, and the Omarchy
  // list is read the first time it is shown.
  function setView(next) {
    if (next === view || editorOpen || !hasTab(next)) return
    var stash = Object.assign({}, viewSelection)
    stash[view] = selectedId
    viewSelection = stash
    view = next
    if (next !== "omarchy") lastUserTab = next
    settingsOpen = false
    restoreSelection()
    if (next === "omarchy" && omarchyCommands.length === 0 && !omarchyLoading) loadOmarchy()
  }

  // Keep the tab's remembered selection if it is still in the list, else the first row.
  function restoreSelection() {
    var pool = currentList
    var keep = viewSelection[view] || selectedId
    for (var i = 0; i < pool.length; i++) if (pool[i].id === keep) { selectedId = keep; return }
    selectedId = pool.length > 0 ? pool[0].id : ""
  }

  // After the tabs change (one deleted, the Omarchy tab turned off) the one
  // showing may be gone: fall back to the first tab.
  function ensureView() {
    if (hasTab(view)) return
    view = tabs[0].id
    lastUserTab = view
    restoreSelection()
  }

  // Shift+J / Shift+K: move the selected item within its tab.
  function moveSelected(delta) {
    if (selectedScript === null || editorOpen) return
    moveItem(selectedId, delta)
  }

  // Also the ↑ ↓ of the Edit forms, which move the item being edited right away.
  function moveItem(id, delta) {
    if (id === "" || engineWriting) return
    engineWriting = true
    engineCall(["move", id], { delta: delta }, function(payload) {
      engineWriting = false
      applyLibrary(payload)
      for (var i = 0; i < currentList.length; i++)
        if (currentList[i].id === id) { list.positionViewAtIndex(i, ListView.Contain); break }
    })
  }

  // h / l (or ← →): show the previous / next tab.
  function stepTab(dx) {
    if (editorOpen) return
    for (var i = 0; i < tabChips.length; i++) if (tabChips[i].id === view) {
      var next = i + dx
      if (next >= 0 && next < tabChips.length) setView(tabChips[next].id)
      return
    }
  }

  // Shift+H / Shift+L: the user tab before / after the selected row's.
  function neighbourTab(dx) {
    if (selectedScript === null) return ""
    var from = selectedScript.tab || "main"
    for (var i = 0; i < tabs.length; i++) if (tabs[i].id === from) {
      var next = i + dx
      return next >= 0 && next < tabs.length ? tabs[next].id : ""
    }
    return ""
  }

  // Shift+H / Shift+L: carry the selected row -- a script, a separator or a
  // Readily command -- to the end of the previous / next tab, and follow it
  // there, so pressing again takes it further. (The Edit form picks any tab.)
  function moveSelectedToTab(tab) {
    if (selectedScript === null || tab === "" || editorOpen || engineWriting) return
    if (tab === (selectedScript.tab || "main")) return
    var id = selectedId
    var at = 0
    for (var i = 0; i < tabScripts.length; i++) if (tabScripts[i].id === id) { at = i; break }
    engineWriting = true
    engineCall(["set-tab", id], { tab: tab }, function(payload) {
      engineWriting = false
      if (!payload || payload.error !== undefined) {
        showNotice(payload && payload.error ? payload.error : "The engine did not answer")
        return
      }
      applyLibrary(payload)
      // The tab left behind remembers the row that took its place.
      if (tabScripts.length > 0) selectedId = tabScripts[Math.min(at, tabScripts.length - 1)].id
      setView(tab)
      selectedId = id
      Qt.callLater(function() { list.positionViewAtIndex(tabScripts.length - 1, ListView.Contain) })
    })
  }

  function loadOmarchy() {
    omarchyLoading = true
    engineCall(["omarchy"], undefined, function(payload) {
      omarchyLoading = false
      if (!payload || payload.error !== undefined) return
      omarchyCommands = Array.isArray(payload.commands) ? payload.commands : []
      omarchyWarning = payload.warning ? String(payload.warning) : ""
      if (view === "omarchy" && selected === null && omarchyVisible.length > 0) selectedId = omarchyVisible[0].id
    })
  }

  // Typing filters the list; the selection moves to the first match when
  // the one it had is filtered out.
  function searchOmarchy(text) {
    omarchyQuery = text
    var pool = omarchyVisible
    for (var i = 0; i < pool.length; i++)
      if (pool[i].id === selectedId) { list.positionViewAtIndex(i, ListView.Contain); return }
    selectedId = pool.length > 0 ? pool[0].id : ""
    list.positionViewAtBeginning()
  }

  function focusSearch() {
    if (!omarchyEnabled) return
    setView("omarchy")
    if (view === "omarchy") omarchySearch.forceActiveFocus()
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
    if (view === "omarchy") { omarchyPane.run(); return }
    engineCall(["run", id, "--cols", String(termCols), "--rows", String(termRows)], undefined, function(payload) {
      if (!payload || payload.error !== undefined) return
      sessionStarted(id)
    })
  }

  function sessionStarted(id) {
    screenEpoch++
    markSession(id, false, null)
    refreshStatus()
  }

  // An Omarchy command runs as its route plus the arguments typed for it,
  // which are remembered so ▶ on a finished one runs the same line again.
  function runOmarchy(id, args) {
    var last = Object.assign({}, omarchyLastArgs)
    last[id] = args
    omarchyLastArgs = last
    engineCall(["omarchy-run", id, "--cols", String(termCols), "--rows", String(termRows)], { args: args }, function(payload) {
      if (!payload || payload.error !== undefined) return
      sessionStarted(id)
    })
  }

  // Omarchy's floating terminal, the one its own menu uses; the panel steps
  // out of the way so the terminal is in front.
  function openInTerminal(commandLine) {
    if (commandLine === "") return
    Quickshell.execDetached(["omarchy-launch-floating-terminal-with-presentation", commandLine])
    close()
  }

  // Copy an Omarchy command into the user's own list through the regular
  // add form, so it can be renamed, edited and scheduled before saving.
  function addFromOmarchy(args) {
    var entry = selected
    if (view !== "omarchy" || entry === null) return
    var usage = entry.route + (entry.args !== "" ? " " + entry.args : "")
    var help = entry.summary !== "" ? entry.summary + "\n\n" : ""
    help += "Omarchy command. Usage: " + usage
    for (var i = 0; i < entry.examples.length; i++) help += "\nExample: " + entry.examples[i]
    var name = entry.name.charAt(0).toUpperCase() + entry.name.substring(1)
    startAdd({ name: name.substring(0, 64), command: entry.route + (args !== "" ? " " + args : ""), help: help })
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
    omarchyPane.setArgs(omarchyLastArgs[selectedId] || "")
  }

  function sendLine(id, text) {
    engineCall(["send", id], { text: text }, function(payload) { pollScreen() })
  }

  function stopScript(id) {
    engineCall(["stop", id], undefined, function(payload) {
      if (!payload || payload.error !== undefined) return
      screenEpoch++
      markSession(id, payload.dead === true, payload.exit === undefined ? null : payload.exit)
      pollScreen()
    })
  }

  function closeScript(id) {
    engineCall(["close", id], undefined, function(payload) {
      // Unconditional, unlike stop/play/applyScreen: a close on a session
      // that is already gone (engine error) must still clear the pane.
      screenEpoch++
      dropSession(id)
      if (screenFor === id) { screenFor = ""; screen = emptyScreen() }
      terminalPane.clearInput()
    })
  }

  // ---- add / edit / delete
  // A new item goes into the user tab showing (the last one shown when the
  // Omarchy tab is up), right below the selected row there.
  // prefill: optional { name, command, help } to start a script form from.
  function startAdd(prefill, kind) {
    setView(userTab())
    editorKind = kind === "separator" ? "separator" : "script"
    editingId = ""
    editingReadily = false
    addAfter = selectedScript !== null ? selectedId : ""
    if (editorKind === "separator") {
      separatorPane.title = "New separator"
      separatorPane.load(null, view)
    } else {
      editorPane.readOnly = false
      editorPane.title = "New script"
      editorPane.load(prefill || null, view)
    }
    editorOpen = true
    settingsOpen = false
  }

  function startEdit() {
    if (selectedScript === null) return
    editingId = selectedScript.id
    editingReadily = (selectedScript.source === "readily")
    editorKind = selectedIsSeparator ? "separator" : "script"
    if (editorKind === "separator") {
      separatorPane.title = "Edit separator"
      separatorPane.load(selectedScript, view)
    } else {
      editorPane.readOnly = editingReadily
      editorPane.title = editingReadily ? "Readily command (schedule and tab)" : "Edit script"
      editorPane.load(selectedScript, view)
    }
    editorOpen = true
    settingsOpen = false
  }

  function cancelEditor() {
    editorOpen = false
    keyCatcher.forceActiveFocus()
  }

  function toggleSettings() {
    settingsOpen = !settingsOpen
    if (settingsOpen) {
      editorOpen = false
      Qt.callLater(function() { settingsPane.forceActiveFocus() })
    }
  }

  function editorError(text) {
    if (editorKind === "separator") separatorPane.errorText = text
    else editorPane.errorText = text
  }

  function saveEditor(fields) {
    if (editingReadily) {
      var readilyId = editingId
      var fromTab = selectedScript !== null && selectedScript.id === readilyId ? (selectedScript.tab || "main") : view
      engineWriting = true
      engineCall(["schedule-set", readilyId], fields.schedule, function(payload) {
        if (!payload || payload.error !== undefined) {
          engineWriting = false
          editorError(payload && payload.error ? payload.error : "The engine did not answer")
          return
        }
        if (payload.schedule_warning) showNotice(payload.schedule_warning)
        refreshSchedules()   // refresh nextRuns
        if (fields.tab === undefined || fields.tab === fromTab) {
          engineWriting = false
          editorOpen = false
          keyCatcher.forceActiveFocus()
          refreshList()      // re-merge the side-store schedule into the Readily row (so ⏰ shows)
          return
        }
        // The form moved it to another tab: the reply is the whole list, schedule included.
        engineCall(["set-tab", readilyId], { tab: fields.tab }, function(moved) {
          engineWriting = false
          if (!moved || moved.error !== undefined) {
            editorError(moved && moved.error ? moved.error : "The engine did not answer")
            return
          }
          applyLibrary(moved)
          editorOpen = false
          setView(fields.tab)
          selectedId = readilyId
          keyCatcher.forceActiveFocus()
        })
      })
      return
    }

    var adding = editingId === ""
    var args = adding ? ["add"] : ["update", editingId]
    var payloadIn = Object.assign({}, fields)
    if (adding) {
      if (payloadIn.tab === undefined) payloadIn.tab = view
      // Below the selection when it stays in this tab; at the end of another one.
      if (addAfter !== "" && payloadIn.tab === view) payloadIn.after = addAfter
    }
    // The ids there before an add, to find the new one afterwards: Readily
    // rows come after the native ones, so it is not necessarily the last row.
    var before = {}
    for (var b = 0; b < scripts.length; b++) before[scripts[b].id] = true
    engineWriting = true
    engineCall(args, payloadIn, function(payload) {
      engineWriting = false
      if (!payload || payload.error !== undefined) {
        editorError(payload && payload.error ? payload.error : "The engine did not answer")
        return
      }
      applyLibrary(payload)
      var savedId = editingId
      if (adding) {
        savedId = ""
        for (var i = 0; i < scripts.length; i++)
          if (!before[scripts[i].id] && scripts[i].source !== "readily") { savedId = scripts[i].id; break }
      }
      editorOpen = false
      // Follow the item to its tab when the form moved it to another one.
      for (var j = 0; j < scripts.length; j++)
        if (scripts[j].id === savedId && (scripts[j].tab || "main") !== view) { setView(scripts[j].tab); break }
      if (savedId !== "") selectedId = savedId
      keyCatcher.forceActiveFocus()
    })
  }

  // The confirm dialog runs whatever action asked for it.
  function askConfirm(message, confirmText, action) {
    confirm.message = message
    confirm.confirmText = confirmText
    confirm.selectedIndex = 0
    confirmAction = action
    confirm.opened = true
    keyCatcher.forceActiveFocus()
  }

  function closeConfirm(confirmed) {
    var action = confirmAction
    confirmAction = null
    confirm.opened = false
    if (settingsOpen) settingsPane.forceActiveFocus()
    if (confirmed && action) action()
  }

  function requestDelete() {
    if (!selectedIsNative) return
    var id = selectedId
    var message = selectedIsSeparator
      ? (selectedScript.label !== "" ? "Delete the \"" + selectedScript.label + "\" separator?" : "Delete this separator?")
      : "Delete \"" + selectedScript.name + "\"?" + (sessionOf(id) !== null ? " Its terminal will be closed." : "")
    askConfirm(message, "Delete", function() { performDelete(id) })
  }

  function performDelete(id) {
    if (id === "") return
    engineWriting = true
    engineCall(["remove", id], undefined, function(payload) {
      engineWriting = false
      if (!payload || payload.error !== undefined) return
      dropSession(id)
      if (screenFor === id) { screenFor = ""; screen = emptyScreen() }
      selectedId = ""
      applyLibrary(payload)
    })
  }

  // ---- tabs and settings: every change goes through the engine and comes
  // back as the whole library (or config).
  function tabCall(args, stdinJson) {
    engineWriting = true
    engineCall(args, stdinJson, function(payload) {
      engineWriting = false
      applyLibrary(payload)
    })
  }

  function requestRemoveTab(id) {
    var name = "", count = 0
    for (var i = 0; i < tabs.length; i++) if (tabs[i].id === id) name = tabs[i].name
    for (var j = 0; j < scripts.length; j++)
      if (scripts[j].tab === id && scripts[j].source !== "readily" && scripts[j].kind !== "separator") count++
    var home = ""
    for (var k = 0; k < tabs.length; k++) if (tabs[k].id === "main") home = tabs[k].name
    askConfirm("Delete the \"" + name + "\" tab?"
      + (count > 0 ? " Its " + (count === 1 ? "command moves" : count + " commands move") + " to \"" + home + "\"." : ""),
      "Delete", function() { tabCall(["tab-remove", id]) })
  }

  function setConfig(patch) {
    engineCall(["set-config"], patch, function(payload) {
      if (payload && payload.error === undefined) {
        config = payload
        ensureView()
        refreshList()
      }
      if (payload && payload.schedule_warning) showNotice(payload.schedule_warning)
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
      engineCall(["resize", selectedId, "--cols", String(termCols), "--rows", String(termRows)], undefined, function(payload) { pollScreen() })
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
      // `undefined` is the "no stdin" sentinel; an explicit `null` (e.g. a
      // Readily schedule turned off) must still be written as the literal
      // JSON `null` line so the engine's stdin.readline() is not left
      // blocked forever (which would deadlock this shared, serialized
      // Process for every later queued call).
      if (call && call.stdin !== undefined) write(JSON.stringify(call.stdin) + "\n")
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
    // The tab showing may have been deleted; the selection must stay in the tab.
    ensureView()
    if (view !== "omarchy") {
      var inTab = false
      for (var i = 0; i < tabScripts.length; i++) if (tabScripts[i].id === selectedId) { inTab = true; break }
      if (!inTab) selectedId = tabScripts.length > 0 ? tabScripts[0].id : ""
    }
    refreshSchedules()
    if (payload.schedule_warning) showNotice(payload.schedule_warning)
  }

  function refreshSchedules() {
    engineCall(["schedules"], undefined, function(p) {
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
    engineCall(["list"], undefined, applyLibrary)
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
      engineCall(["config"], undefined, function(p) { if (p && p.error === undefined) runbook.config = p })
      // Re-read on every open while it is showing: an Omarchy update can change the list.
      if (view === "omarchy") loadOmarchy()
    } else {
      confirm.opened = false
      editorOpen = false
      settingsOpen = false
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
      blocked: (runbook.editorOpen || runbook.settingsOpen || runbook.inputFocused) && !confirm.opened
      onCloseRequested: {
        if (confirm.opened) runbook.closeConfirm(false)
        else if (runbook.editorOpen) runbook.cancelEditor()
        else if (runbook.settingsOpen) runbook.settingsOpen = false
        else runbook.close()
      }
      onTabRequested: function(direction) {
        if (confirm.opened) confirm.selectedIndex = confirm.selectedIndex === 0 ? 1 : 0
        else runbook.switchPanel(direction)
      }
      onMoveRequested: function(dx, dy) {
        if (confirm.opened) { if (dx !== 0) confirm.selectedIndex = confirm.selectedIndex === 0 ? 1 : 0; return }
        if (dy !== 0) runbook.moveSelection(dy)
        if (dx !== 0) runbook.stepTab(dx)
      }
      onActivateRequested: {
        // Enter only answers the dialog, or moves to an Omarchy command's
        // arguments line; it never runs a script.
        if (!confirm.opened) { if (runbook.mode === "omarchy") omarchyPane.focusArgs(); return }
        runbook.closeConfirm(confirm.selectedIndex !== 0)
      }
      onDeleteRequested: { if (!confirm.opened && runbook.mode !== "editor") runbook.requestDelete() }
      onTextKey: function(text) {
        if (confirm.opened) return
        if (text === "c" && runbook.mode === "terminal") terminalPane.copyOutput()
        if (text === "/" && runbook.mode !== "editor") runbook.focusSearch()
        // Shift+J / Shift+K move the selected item down / up within its tab.
        if (text === "J") runbook.moveSelected(1)
        if (text === "K") runbook.moveSelected(-1)
        // Shift+H / Shift+L send it to the previous / next tab.
        if (text === "H") runbook.moveSelectedToTab(runbook.neighbourTab(-1))
        if (text === "L") runbook.moveSelectedToTab(runbook.neighbourTab(1))
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
            meta: {
              if (runbook.view === "omarchy") return runbook.omarchyCommands.length + " Omarchy commands"
              var n = runbook.tabScripts.filter(function(s) { return s.kind !== "separator" }).length
              return n === 1 ? "1 script" : n + " scripts"
            }
            foreground: runbook.foreground
            fontFamily: runbook.fontFamily

            trailingControl: Component {
              Row {
                spacing: Style.space(6)
                PanelActionButton {
                  iconText: "+"
                  tooltipText: "Add a command"
                  enabled: runbook.mode !== "editor"
                  foreground: runbook.foreground
                  hoverColor: runbook.accent
                  onClicked: runbook.startAdd()
                }
                PanelActionButton {
                  iconText: "―"
                  tooltipText: "Add a separator below the selection"
                  enabled: runbook.mode !== "editor"
                  foreground: runbook.foreground
                  hoverColor: runbook.accent
                  onClicked: runbook.startAdd(null, "separator")
                }
                PanelActionButton {
                  iconText: "⚙"
                  tooltipText: "Settings"
                  enabled: runbook.mode !== "editor"
                  foreground: runbook.foreground
                  hoverColor: runbook.accent
                  onClicked: runbook.toggleSettings()
                }
              }
            }
          }

          // The tabs, wrapping onto more lines when there are many.
          Flow {
            Layout.fillWidth: true
            spacing: Style.space(6)
            Repeater {
              model: runbook.tabChips
              delegate: Button {
                required property var modelData
                text: modelData.name
                bordered: true
                selected: runbook.view === modelData.id
                enabled: runbook.mode !== "editor"
                foreground: runbook.foreground
                accent: runbook.accent
                fontFamily: runbook.fontFamily
                tooltipText: modelData.id === "omarchy" ? "Omarchy's own commands (/ to search)" : ""
                onClicked: runbook.setView(modelData.id)
              }
            }
          }

          TextField {
            id: omarchySearch
            Layout.fillWidth: true
            visible: runbook.view === "omarchy"
            placeholderText: "Search (/)"
            foreground: runbook.foreground
            accent: runbook.accent
            onTextChanged: runbook.searchOmarchy(text)
            // Enter or ↓ hands the keyboard back to the list, on the first match.
            // The key is consumed: a TextField lets Enter through to its parents
            // after "accepted", and the list would take it as a second Enter
            // (the arguments line), one Enter away from running the command.
            Keys.onReturnPressed: function(event) { keyCatcher.forceActiveFocus(); event.accepted = true }
            Keys.onEnterPressed: function(event) { keyCatcher.forceActiveFocus(); event.accepted = true }
            Keys.onDownPressed: function(event) { keyCatcher.forceActiveFocus(); event.accepted = true }
            Keys.onEscapePressed: function(event) {
              if (text !== "") text = ""
              else keyCatcher.forceActiveFocus()
              event.accepted = true
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
            model: runbook.currentList
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

          Text {
            Layout.fillWidth: true
            visible: runbook.view === "omarchy" && runbook.omarchyQuery.trim() !== ""
            text: runbook.omarchyVisible.length + " of " + runbook.omarchyCommands.length
            textFormat: Text.PlainText
            color: runbook.dim
            font.family: runbook.fontFamily
            font.pixelSize: Style.font.bodySmall
          }

          RowLayout {
            Layout.fillWidth: true
            visible: runbook.view !== "omarchy"
            spacing: Style.space(6)
            Button {
              text: "Edit"
              bordered: true
              enabled: runbook.selectedScript !== null && runbook.mode !== "editor"
              foreground: runbook.foreground
              fontFamily: runbook.fontFamily
              onClicked: runbook.startEdit()
            }
            Button {
              text: "Delete"
              bordered: true
              visible: !(runbook.selectedScript && runbook.selectedScript.source === "readily")
              enabled: runbook.selectedIsNative && runbook.mode !== "editor"
              foreground: runbook.urgent
              fontFamily: runbook.fontFamily
              onClicked: runbook.requestDelete()
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
                visible: runbook.selectedScript === null
                text: runbook.tabScripts.length === 0 ? "Add a command with +" : "Select a script"
                textFormat: Text.PlainText
                color: runbook.dim
                font.family: runbook.fontFamily
                font.pixelSize: Style.font.body
              }

              Text {
                width: parent.width
                visible: runbook.selectedIsSeparator
                text: runbook.selectedIsSeparator
                  ? (runbook.selectedScript.label !== "" ? "Separator \u201c" + runbook.selectedScript.label + "\u201d" : "Separator (no label)")
                    + "\n\nEdit changes its label. Shift+J / Shift+K or the \u2191 \u2193 buttons move it; Delete removes it."
                  : ""
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                color: runbook.dim
                font.family: runbook.fontFamily
                font.pixelSize: Style.font.body
              }

              Text {
                width: parent.width
                visible: runbook.helpScript !== null
                text: runbook.helpScript ? runbook.helpScript.command : ""
                textFormat: Text.PlainText
                wrapMode: Text.WrapAnywhere
                color: runbook.foreground
                font.family: runbook.monoFamily
                font.pixelSize: Style.font.body
              }

              PanelSeparator {
                width: parent.width
                visible: runbook.helpScript !== null
                foreground: runbook.foreground
              }

              Text {
                width: parent.width
                visible: runbook.helpScript !== null
                text: runbook.helpScript && runbook.helpScript.help !== "" ? runbook.helpScript.help : "No help text yet. Use Edit to add one."
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                color: runbook.helpScript && runbook.helpScript.help !== "" ? runbook.foreground : runbook.dim
                font.family: runbook.fontFamily
                font.pixelSize: Style.font.body
              }

              Text {
                width: parent.width
                visible: runbook.helpScript !== null && runbook.helpScript.schedule ? true : false
                text: {
                  if (!runbook.helpScript || !runbook.helpScript.schedule) return ""
                  var base = runbook.describeSchedule(runbook.helpScript.schedule)
                  var nr = runbook.nextRuns[runbook.helpScript.id]
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

          SettingsPane {
            id: settingsPane
            anchors.fill: parent
            visible: runbook.mode === "settings"
            focus: visible
            config: runbook.config
            tabs: runbook.tabs
            foreground: runbook.foreground
            accent: runbook.accent
            urgent: runbook.urgent
            dim: runbook.dim
            fontFamily: runbook.fontFamily
            onCloseRequested: runbook.settingsOpen = false
            onConfigRequested: function(patch) { runbook.setConfig(patch) }
            onAddTabRequested: function(name) { runbook.tabCall(["tab-add"], { name: name }) }
            onRenameTabRequested: function(id, name) { runbook.tabCall(["tab-rename", id], { name: name }) }
            onMoveTabRequested: function(id, delta) { runbook.tabCall(["tab-move", id], { delta: delta }) }
            onRemoveTabRequested: function(id) { runbook.requestRemoveTab(id) }
          }

          OmarchyPane {
            id: omarchyPane
            anchors.fill: parent
            visible: runbook.mode === "omarchy"
            command: runbook.view === "omarchy" ? runbook.selected : null
            emptyText: runbook.omarchyLoading && runbook.omarchyCommands.length === 0 ? "Loading Omarchy's commands…"
              : runbook.omarchyWarning !== "" ? runbook.omarchyWarning
              : runbook.omarchyCommands.length > 0 && runbook.omarchyVisible.length === 0
                ? "No command matches \"" + runbook.omarchyQuery.trim() + "\""
              : "Select a command"
            foreground: runbook.foreground
            accent: runbook.accent
            urgent: runbook.urgent
            dim: runbook.dim
            fontFamily: runbook.fontFamily
            monoFamily: runbook.monoFamily
            onRunRequested: function(args) { runbook.runOmarchy(runbook.selectedId, args) }
            onTerminalRequested: function(commandLine) { runbook.openInTerminal(commandLine) }
            onAddRequested: function(args) { runbook.addFromOmarchy(args) }
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
            visible: runbook.mode === "editor" && runbook.editorKind === "script"
            tabs: runbook.tabs
            canMove: runbook.editingId !== ""
            foreground: runbook.foreground
            accent: runbook.accent
            urgent: runbook.urgent
            dim: runbook.dim
            fontFamily: runbook.fontFamily
            monoFamily: runbook.monoFamily
            onMoveRequested: function(delta) { runbook.moveItem(runbook.editingId, delta) }
            onSaveRequested: function(fields) { runbook.saveEditor(fields) }
            onCancelRequested: runbook.cancelEditor()
          }

          SeparatorPane {
            id: separatorPane
            tabs: runbook.tabs
            canMove: runbook.editingId !== ""
            onMoveRequested: function(delta) { runbook.moveItem(runbook.editingId, delta) }
            anchors.fill: parent
            visible: runbook.mode === "editor" && runbook.editorKind === "separator"
            foreground: runbook.foreground
            accent: runbook.accent
            urgent: runbook.urgent
            dim: runbook.dim
            fontFamily: runbook.fontFamily
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
        onCanceled: runbook.closeConfirm(false)
        onConfirmed: runbook.closeConfirm(true)
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
