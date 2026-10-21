# Polaris Curator – Omarchy bar widget

Theme-aware control panel for the Polaris Curator. It shows pending ChatGPT
messages and project inbox documents, starts one project or all open projects,
reports the result, exposes the automatic timer state, and opens the curated
knowledge files.

The widget is intentionally a thin UI. Project discovery, status calculation,
locking, budgets, LLM execution, atomic merges, and inbox archiving stay in the
`polaris-curator` CLI.

## Installation

`./install.sh` in the parent repository does all of this (copy, enable,
backup of a replaced copy). Manually, from the parent
`polaris-chatgpt-connector` repository:

```bash
install -d ~/.config/omarchy/plugins/mst.polaris-curator
cp -a "$PWD/omarchy-plugin/mst.polaris-curator/." \
  ~/.config/omarchy/plugins/mst.polaris-curator/
omarchy plugin enable mst.polaris-curator center
```

Omarchy's plugin validator intentionally rejects a plugin directory that is
itself a symlink, so the active installation is a copy. After updating this
repository, sync it with `./install.sh --yes --no-units --no-backup --no-memory`
(backs up the old copy), then `omarchy restart shell`. The health check shows
a "Widget" part under Curator that turns yellow while the copy is outdated.

Manual runs ("Kuratieren", "Alle") start as the transient systemd user unit
`polaris-curator-manual` (`bin/run-action`), not as a child of the shell: they
survive `omarchy restart shell`, run in the same environment as the timer, and
the fixed unit name prevents a second concurrent manual run. The JSON result
goes to `~/.local/state/polaris/manual-run.json`; the log is in
`journalctl --user -u polaris-curator-manual`. After a shell restart the widget
picks up a still running manual run and shows its result when it ends.

The plugin expects `polaris-curator`, `jq`, `systemctl`, `systemd-run`, and `xdg-open` on
`PATH`. It uses no native Qt file dialog because that integration is unstable
in Quickshell on the target system.

## Status semantics

- muted dot: all projects are current
- small accent dot: pending source units
- accent spinner: a manual run is active
- urgent dot: status or the last manual action failed

`backfill` is deliberately not exposed: it can reprocess already curated
messages and create redundant knowledge entries.
