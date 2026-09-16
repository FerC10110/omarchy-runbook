import QtQuick
import qs.Commons
import qs.Ui

// Bar entry point for Runbook. The icon is the only thing the bar sees; the
// panel is loaded lazily and injected with the bar context, like Save Them All.
// Left click opens/closes the panel; right click forces a status refresh.
BarWidget {
  id: root
  moduleName: "io.github.ferc10110.runbook"

  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property int runningCount: panelLoader.item ? panelLoader.item.runningCount : 0
  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false

  function open() { if (panelLoader.item) panelLoader.item.open() }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  function togglePanel() { if (panelLoader.item) panelLoader.item.toggle() }
  function closeForPopoutSwitch() { if (panelLoader.item) panelLoader.item.closeForPopoutSwitch() }

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "\u{F018D}"
    slotSize: Style.bar.statusSlot
    tooltipText: root.runningCount > 0
      ? "Runbook · " + root.runningCount + " running"
      : "Runbook"
    // Accent while something runs; the bar's own colour otherwise.
    active: root.runningCount > 0
    onPressed: function(b) {
      if (b === Qt.RightButton) {
        if (panelLoader.item) panelLoader.item.refreshStatus()
      } else {
        root.togglePanel()
      }
    }
  }
}
