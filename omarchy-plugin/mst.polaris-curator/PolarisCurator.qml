pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "mst.polaris-curator"
  ipcTarget: "mst.polaris-curator"

  readonly property string pluginDir: Quickshell.env("HOME") + "/.config/omarchy/plugins/mst.polaris-curator"

  property bool installed: true
  property bool refreshing: false
  property bool actionRunning: false
  property bool statusError: false
  property string actionTarget: ""
  property string lastResult: ""
  property bool lastResultOk: true
  property var projects: []
  property var summary: ({})
  property var timerState: ({})
  property var health: ({})
  property var systemHealth: ({})

  implicitWidth: icon.implicitWidth
  implicitHeight: root.bar ? root.bar.barSize : Style.bar.sizeHorizontal

  function pendingUnits() {
    return Number(root.summary.pending_units || 0)
  }

  function pendingProjects() {
    return Number(root.summary.pending_projects || 0)
  }

  function healthWarnings() {
    return (root.health && root.health.warnings) ? root.health.warnings : []
  }

  function levelColor(level) {
    if (level === "error") return Color.urgent
    if (level === "warn") return "#e0a030"
    return "#4caf50"
  }

  function overallLevel() {
    return (root.systemHealth && root.systemHealth.level) ? root.systemHealth.level : "ok"
  }

  function firstProblem() {
    var all = ((root.systemHealth.components || []).concat(root.systemHealth.system || []))
    for (var i = 0; i < all.length; i++)
      if (all[i].level === "error") return all[i]
    for (var j = 0; j < all.length; j++)
      if (all[j].level === "warn") return all[j]
    return null
  }

  function systemSummary() {
    var items = root.systemHealth.system || []
    var parts = []
    for (var i = 0; i < items.length; i++)
      parts.push(items[i].label + ": " + items[i].detail)
    return parts.join(" · ")
  }

  function statusColor() {
    if (root.overallLevel() === "error") return Color.urgent
    if (root.overallLevel() === "warn") return "#e0a030"
    if (root.statusError || !root.lastResultOk || root.healthWarnings().length > 0) return Color.urgent
    if (root.actionRunning || root.pendingUnits() > 0) return Color.accent
    return Color.muted
  }

  function formatCount(value, singular, plural) {
    var n = Number(value || 0)
    return n + " " + (n === 1 ? singular : plural)
  }

  function projectSources(project) {
    var parts = []
    if (Number(project.new_messages || 0) > 0)
      parts.push(formatCount(project.new_messages, "Chat-Nachricht", "Chat-Nachrichten"))
    if (Number(project.new_documents || 0) > 0)
      parts.push(formatCount(project.new_documents, "Dokument", "Dokumente"))
    if (Number(project.new_emails || 0) > 0)
      parts.push(formatCount(project.new_emails, "E-Mail", "E-Mails"))
    if (Number(project.new_notes || 0) > 0)
      parts.push(formatCount(project.new_notes, "Notiz", "Notizen"))
    return parts.length > 0 ? parts.join(" · ") : "Aktuell"
  }

  function projectPlan(project) {
    var calls = Number(project.planned_llm_calls || 0)
    var text = calls + (calls === 1 ? " geplanter LLM-Aufruf" : " geplante LLM-Aufrufe")
    if (Number(project.backlog_units || 0) > 0)
      text += " · " + project.backlog_units + " danach weiter offen"
    if (project.last_successful_run)
      text += " · zuletzt " + Qt.formatDateTime(new Date(project.last_successful_run), "dd.MM. HH:mm")
    return text
  }

  function applyStatus(raw) {
    root.refreshing = false
    try {
      var data = JSON.parse(String(raw || "{}"))
      root.installed = data.installed !== false
      root.statusError = Boolean(data.error) || !root.installed
      root.projects = data.projects || []
      root.summary = data.summary || {}
      root.timerState = data.timer || {}
      root.health = data.health || {}
      root.systemHealth = data.system_health || {}
      if (data.error) root.lastResult = String(data.error)
    } catch (e) {
      root.statusError = true
      root.lastResultOk = false
      root.lastResult = "Curator-Status konnte nicht gelesen werden."
    }
  }

  function refreshStatus() {
    if (statusProc.running) return
    root.refreshing = true
    statusProc.running = true
  }

  function runProject(name) {
    if (root.actionRunning) return
    root.actionRunning = true
    root.actionTarget = name
    root.lastResult = ""
    actionProc.command = [root.pluginDir + "/bin/run-action", "run-project", name]
    actionProc.running = true
  }

  function runAll() {
    if (root.actionRunning) return
    root.actionRunning = true
    root.actionTarget = "Alle Projekte"
    root.lastResult = ""
    actionProc.command = [root.pluginDir + "/bin/run-action", "run-all"]
    actionProc.running = true
  }

  function openTarget(project, kind) {
    if (openProc.running) return
    openProc.command = [root.pluginDir + "/bin/run-action", "open", project, kind]
    openProc.running = true
  }

  function summarizeAction(data) {
    if (data.report) {
      var report = data.report
      var processed = Number((report.day_batches_processed || []).length)
        + Number((report.documents_processed || []).length)
      var changed = []
      if (report.knowledge_changed) changed.push("Wissen")
      if (report.decisions_changed) changed.push("Entscheidungen")
      if (report.open_loops_changed) changed.push("offene Punkte")
      if (report.aborted_reason) return report.project + ": " + report.aborted_reason
      return report.project + ": " + processed + (processed === 1 ? " Einheit" : " Einheiten") + " verarbeitet"
        + (changed.length > 0 ? " · geändert: " + changed.join(", ") : " · keine Wissensänderung")
    }
    if (data.reports) {
      var units = 0
      var changedProjects = 0
      for (var i = 0; i < data.reports.length; i++) {
        var r = data.reports[i].report || {}
        units += Number((r.day_batches_processed || []).length) + Number((r.documents_processed || []).length)
        if (r.knowledge_changed || r.decisions_changed || r.open_loops_changed) changedProjects++
      }
      return data.reports.length + " Projekte geprüft · " + units + " Einheiten verarbeitet · "
        + changedProjects + " Projekte geändert"
    }
    return data.error || "Aktion abgeschlossen."
  }

  Process {
    id: statusProc
    command: [root.pluginDir + "/bin/status-json"]
    stdout: StdioCollector { id: statusStdout; waitForEnd: true }
    onExited: function(exitCode) {
      root.refreshing = false
      if (exitCode === 0) root.applyStatus(statusStdout.text)
      else {
        root.statusError = true
        root.lastResultOk = false
        root.lastResult = "Curator-Statusprozess ist fehlgeschlagen."
      }
    }
  }

  Process {
    id: actionProc
    stdout: StdioCollector { id: actionStdout; waitForEnd: true }
    onExited: function(exitCode) {
      root.actionRunning = false
      try {
        var data = JSON.parse(String(actionStdout.text || "{}"))
        root.lastResultOk = data.ok === true && exitCode === 0
        root.lastResult = root.summarizeAction(data)
      } catch (e) {
        root.lastResultOk = false
        root.lastResult = "Curator-Lauf ist ohne lesbares Ergebnis beendet worden."
      }
      root.actionTarget = ""
      root.refreshStatus()
    }
  }

  // Nach einem Shell-Neustart: laeuft noch ein manueller Lauf (eigene
  // systemd-Unit), auf sein Ende warten und das Ergebnis anzeigen.
  Process {
    id: manualActiveProc
    command: [root.pluginDir + "/bin/run-action", "manual-active"]
    onExited: function(exitCode) {
      if (exitCode !== 0 || root.actionRunning) return
      root.actionRunning = true
      root.actionTarget = "manueller Lauf"
      root.lastResult = ""
      actionProc.command = [root.pluginDir + "/bin/run-action", "wait-manual"]
      actionProc.running = true
    }
  }

  Process {
    id: openProc
    stdout: StdioCollector { id: openStdout; waitForEnd: true }
    onExited: function(exitCode) {
      var ok = exitCode === 0
      var error = ""
      try {
        var data = JSON.parse(String(openStdout.text || "{}"))
        ok = ok && data.ok === true
        error = String(data.error || "")
      } catch (e) {
        ok = false
      }
      if (!ok) {
        root.lastResultOk = false
        root.lastResult = error || "Datei oder Projektordner konnte nicht geöffnet werden."
      }
    }
  }

  // Alle 10 min (seit 2026-10-09, vorher 2 min: Lastspitzen auf dem Drive-Laufwerk)
  // und zusaetzlich beim Oeffnen, damit das Popup immer aktuell ist.
  Timer {
    interval: 10 * 60 * 1000
    repeat: true
    running: true
    triggeredOnStart: true
    onTriggered: root.refreshStatus()
  }

  onOpenedChanged: {
    if (root.opened) {
      flick.contentY = 0
      root.refreshStatus()
    }
  }

  WidgetButton {
    id: icon
    bar: root.bar
    text: "\uf21e"
    fixedWidth: root.bar ? root.bar.barSize : Style.bar.sizeHorizontal
    tooltipText: !root.installed ? "Polaris Curator ist nicht installiert"
      : root.firstProblem() ? root.firstProblem().label + ": " + root.firstProblem().detail
      : root.healthWarnings().length > 0 ? "Polaris: " + root.healthWarnings()[0]
      : root.actionRunning ? "Polaris Curator läuft: " + root.actionTarget
      : root.pendingUnits() > 0
        ? "Alles in Ordnung · " + root.pendingProjects() + " Projekte mit offenen Quellen"
        : "Health Check · alles in Ordnung"

    Rectangle {
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      anchors.rightMargin: 6
      anchors.bottomMargin: 6
      width: 6
      height: 6
      radius: 3
      color: root.statusColor()
      border.width: 1
      border.color: root.bar ? root.bar.background : Color.background
      visible: root.statusError || !root.lastResultOk || root.actionRunning || root.pendingUnits() > 0
        || root.healthWarnings().length > 0 || root.overallLevel() !== "ok"
    }

    onPressed: function(button) {
      root.refreshStatus()
      root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: icon
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(460))
    contentHeight: panel.fittedContentHeight(Style.space(580), Style.space(680))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()

      Flickable {
        id: flick
        anchors.fill: parent
        contentWidth: width
        contentHeight: content.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        ColumnLayout {
          id: content
          width: flick.width
          spacing: Style.spacing.md

          RowLayout {
            Layout.fillWidth: true
            Text {
              text: "Health Check"
              color: Color.foreground
              font.family: Style.font.family
              font.pixelSize: Style.font.subtitle
              font.bold: true
            }
            Item { Layout.fillWidth: true }
            Button {
              text: ""
              iconText: "⟳"
              iconSpinning: root.refreshing
              tooltipText: "Status aktualisieren"
              onClicked: root.refreshStatus()
            }
            Button { text: "✕"; tooltipText: "Schließen"; onClicked: root.close() }
          }

          PanelSeparator { foreground: Color.foreground }

          // Eigene Bausteine mit Ampel (polaris-curator health).
          ColumnLayout {
            Layout.fillWidth: true
            spacing: Style.spacing.xs
            Repeater {
              model: root.systemHealth.components || []
              delegate: RowLayout {
                required property var modelData
                Layout.fillWidth: true
                spacing: Style.spacing.sm
                Rectangle {
                  Layout.alignment: Qt.AlignTop
                  Layout.topMargin: 5
                  width: 8; height: 8; radius: 4
                  color: root.levelColor(modelData.level)
                }
                ColumnLayout {
                  Layout.fillWidth: true
                  spacing: 0
                  Text {
                    text: modelData.label
                    color: Color.foreground
                    font.family: Style.font.family
                    font.pixelSize: Style.font.body
                    font.bold: modelData.level !== "ok"
                  }
                  Text {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: modelData.detail
                    color: modelData.level === "ok" ? Color.muted : root.levelColor(modelData.level)
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                  }
                }
              }
            }
            Text {
              Layout.fillWidth: true
              wrapMode: Text.Wrap
              visible: text.length > 0
              text: root.systemSummary()
              color: Color.muted
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
          }

          PanelSeparator { foreground: Color.foreground }

          Text {
            text: "Polaris Curator"
            color: Color.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.body
            font.bold: true
          }

          // Stille Ausfaelle der ChatGPT-Archivierung (health.py), z. B. seit
          // Tagen keine Nachricht mehr in irgendeinem Projekt.
          ColumnLayout {
            Layout.fillWidth: true
            spacing: Style.spacing.xs
            visible: root.healthWarnings().length > 0
            Text {
              text: "Archivierung prüfen"
              color: Color.urgent
              font.family: Style.font.family
              font.pixelSize: Style.font.body
              font.bold: true
            }
            Repeater {
              model: root.healthWarnings()
              delegate: Text {
                required property var modelData
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: "• " + modelData
                color: Color.urgent
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
              }
            }
          }

          ColumnLayout {
            Layout.fillWidth: true
            spacing: Style.spacing.xs
            Text {
              text: root.statusError ? "Status nicht verfügbar"
                : root.pendingUnits() > 0
                  ? root.pendingProjects() + " von " + Number(root.summary.project_count || 0) + " Projekten offen"
                  : "Alle Projekte sind aktuell"
              color: root.statusError ? Color.urgent : root.pendingUnits() > 0 ? Color.accent : Color.foreground
              font.family: Style.font.family
              font.pixelSize: Style.font.body
              font.bold: true
            }
            Text {
              Layout.fillWidth: true
              wrapMode: Text.Wrap
              text: root.pendingUnits() > 0
                ? root.formatCount(root.summary.new_messages, "neue Chat-Nachricht", "neue Chat-Nachrichten")
                  + " · " + root.formatCount(root.summary.new_documents, "neue Inbox-Datei", "neue Inbox-Dateien")
                  + " · " + root.formatCount(root.summary.planned_llm_calls, "LLM-Aufruf", "LLM-Aufrufe")
                : "Neue ChatGPT-Nachrichten und Dateien aus inbox/{documents,mail,notes} erscheinen hier automatisch."
              color: Color.muted
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
            Text {
              Layout.fillWidth: true
              wrapMode: Text.Wrap
              text: root.timerState.enabled
                ? "Automatik aktiv" + (root.timerState.next_run ? " · nächster Lauf " + root.timerState.next_run : "")
                : "Automatischer Timer ist nicht aktiv"
              color: Color.muted
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
          }

          Button {
            Layout.fillWidth: true
            text: root.actionRunning && root.actionTarget === "Alle Projekte"
              ? "Alle Projekte werden kuratiert…" : "Alle offenen Projekte kuratieren"
            bordered: true
            iconSpinning: root.actionRunning && root.actionTarget === "Alle Projekte"
            opacity: !root.actionRunning && root.pendingUnits() > 0 ? 1.0 : 0.45
            onClicked: if (!root.actionRunning && root.pendingUnits() > 0) root.runAll()
          }

          Text {
            visible: root.lastResult !== ""
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: root.lastResult
            color: root.lastResultOk ? Color.muted : Color.urgent
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }

          PanelSeparator { foreground: Color.foreground }
          PanelSectionHeader { text: "PROJEKTE"; foreground: Color.foreground }

          Repeater {
            model: root.projects

            delegate: ColumnLayout {
              required property var modelData
              Layout.fillWidth: true
              spacing: Style.spacing.xs

              RowLayout {
                Layout.fillWidth: true
                Text {
                  text: modelData.name
                  color: Color.foreground
                  font.family: Style.font.family
                  font.pixelSize: Style.font.body
                  font.bold: Number(modelData.pending_units || 0) > 0
                }
                Item { Layout.fillWidth: true }
                Text {
                  text: Number(modelData.pending_units || 0) > 0 ? modelData.pending_units + " offen" : "aktuell"
                  color: Number(modelData.pending_units || 0) > 0 ? Color.accent : Color.muted
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                }
              }

              Text {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: root.projectSources(modelData)
                color: Color.muted
                font.family: Style.font.family
                font.pixelSize: Style.font.bodySmall
              }
              Text {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: root.projectPlan(modelData)
                color: Color.muted
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
              }

              Flow {
                Layout.fillWidth: true
                spacing: Style.spacing.controlGap
                Button {
                  text: root.actionRunning && root.actionTarget === modelData.name ? "Läuft…" : "Kuratieren"
                  bordered: true
                  iconSpinning: root.actionRunning && root.actionTarget === modelData.name
                  opacity: !root.actionRunning && Number(modelData.pending_units || 0) > 0 ? 1.0 : 0.45
                  onClicked: if (!root.actionRunning && Number(modelData.pending_units || 0) > 0) root.runProject(modelData.name)
                }
                Button { text: "Wissen"; onClicked: root.openTarget(modelData.name, "knowledge") }
                Button { text: "Entscheidungen"; onClicked: root.openTarget(modelData.name, "decisions") }
                Button { text: "Offene Punkte"; onClicked: root.openTarget(modelData.name, "open-loops") }
                Button { text: "Ordner"; onClicked: root.openTarget(modelData.name, "folder") }
              }

              PanelSeparator { strength: 0.08 }
            }
          }

          Text {
            visible: root.projects.length === 0 && !root.refreshing
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: "Keine gemappten Polaris-Projekte gefunden."
            color: Color.muted
            font.family: Style.font.family
            font.pixelSize: Style.font.bodySmall
          }
        }
      }
    }
  }

  Component.onCompleted: {
    root.refreshStatus()
    manualActiveProc.running = true
  }
}
