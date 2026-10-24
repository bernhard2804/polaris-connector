# Änderungen

Was sich je Stand für Nutzer ändert. Die Versionsnummer ist die der
Firefox-Erweiterung (`extension/manifest.json`), unter ihr ändern sich auch
Bridge, Curator, Installer und Werkzeuge. Hier stehen vor allem Dinge, die
`install.sh` bzw. `polaris update` ungefragt mitändern oder die eine Nacharbeit
verlangen. Einzelheiten stehen in der README.

## 0.4.4 (2026-10-23)

- **Speicherwächter:** Der Native Host misst den größten Firefox-Tab-Prozess.
  Ab 4 GiB kommt eine Desktop-Meldung, ab 8 GiB eine dringende, dazu eine
  Hinweisleiste in ChatGPT- und Claude-Tabs.
- **`polaris update`** für git-Checkouts: `git fetch`, nur Fast-Forward,
  `install.sh --yes`, Health-Check, bei Fehler zurück. Neue Health-Zeile
  „Update“ (höchstens einmal am Tag `git fetch`). Wer vom Installationspaket
  kommt: README „Umstieg vom Paket auf den git-Checkout“.
- **Laufwerk als Drop-in:** Units hängen nicht mehr fest an
  `rclone-projekte.service`, sondern an
  `~/.config/systemd/user/polaris-*.service.d/polaris-storage.conf`
  (`install.sh --storage-unit <unit|none>`). Lokale Ordner: `none`.
- **`polaris-awake`:** Widget, Bridge und Gedächtnis-Spiegel greifen bei
  Sperre/Einschlafen nicht auf das Laufwerk zu (`ExecCondition=`).

## 0.4.3 (2026-10-21)

- Popup meldet Projektübersicht und Chats ohne Projekt eindeutig (PR #1).
- Altmarkup-Zweig der ChatGPT-Erfassung entfernt.

## 0.4.2 (2026-10-21)

- Erste Version mit `update_url`: Firefox aktualisiert die Erweiterung ab hier
  selbst über den öffentlichen Spiegel. 0.4.1 und älter einmal von Hand
  installieren.
- Widgets für Qt 6.12 angepasst (Omarchy-Kanal `rc`).

## Stand 2026-10-16/17 (Installer und Gedächtnis)

- **Neuer Skill `loesungsqualitaet`:** `install.sh` legt ihn unter
  `~/.config/ai-agents/skills/` ab, und `memlint.py --link` verlinkt ihn
  **ungefragt** in die Skill-Ordner aller eingerichteten Agenten
  (`~/.agents/skills`, `~/.claude/skills`, `~/.codex/skills`). Wer das nicht
  will, entfernt die Links. Dann `memlint` nicht mit `--link` ausführen, sonst
  kommen sie wieder.
- **memlint prüft mehr:** `type=pattern` nur in `knowledge/` und nur mit Beleg
  (`ref=history:`/`commit:`/`url:`), Skill-Köpfe, Skill-Links. `install.sh`
  ersetzt `memlint.py` bei jedem Lauf. Ein Gedächtnis, das vorher nie
  geprüft wurde, zeigt Fehler deshalb oft erst nach dem Update. Häufigster
  Fall: Im Handoff stehen Abschnitte mit `status=superseded`, `historical`
  usw. Diese Regel gilt schon seit 2026-10-08. Solche Abschnitte gehören nach
  `knowledge/` bzw. `history/`, im Handoff steht nur `status=current`.
  Danach `python3 ~/.config/ai-agents/tools/memlint.py` (Exit 0).
- Curator wartet nach ergebnislosen Läufen länger bis zur nächsten Verdichtung.

## Installationspaket 2026-10-15

- Installer richtet Agenten-Starter (`mst.agent-launchers`) und Dock mit ein
  und startet die Leiste am Ende neu. Native Host ohne `~/.mozilla`.
- omarchy-backup und beide Widgets liegen nur im Paket unter `extras/`, nicht
  im öffentlichen Spiegel.
