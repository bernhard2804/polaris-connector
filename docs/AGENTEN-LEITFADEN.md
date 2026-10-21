# Leitfaden für lokale AI-Agenten: Polaris auf diesem Rechner

Dieser Leitfaden richtet sich an einen AI-Agenten (Claude Code, Codex, Gemini u. a.), der auf einem Omarchy-Rechner Polaris einrichten, reparieren oder weiterführen soll. Insbesondere dann, wenn `install.sh` abgebrochen ist. Er beschreibt die Prinzipien, nach denen das System gebaut ist, und wie man sicher weitermacht.

**Zuerst lesen:**
- `~/.config/ai-agents/AI_HANDOFF.md` (Index des Maschinengedächtnisses)
- `handoff/polaris.md`: Was wurde installiert, wo liegt das Protokoll, was ist offen?
- Das neueste Protokoll `~/.local/state/polaris/install-*.log`. Am Ende steht, ob die Installation abgeschlossen wurde oder an welchem Schritt sie abbrach.

## Prinzipien (gelten für jede Änderung)

1. **Erst den Zustand prüfen, dann ändern.** Konfiguration, Units, Links und Protokoll lesen. Nichts aus Annahmen heraus neu anlegen.
2. **Klein, nachvollziehbar, reversibel.** Keine Datei blind ersetzen. Vor dem Ersetzen sichern. `install.sh` legt Ersetztes unter `~/.local/share/polaris/install-backups/<Zeit>/` ab.
3. **Idempotent weitermachen.** `install.sh` kann beliebig oft laufen. Was schon stimmt, meldet es als „ok“ und lässt es in Ruhe. Nach einem Abbruch: Ursache beheben, dann `install.sh` erneut ausführen, zuerst mit `--dry-run`. Nicht von Hand nachbauen, was der Installer kann.
4. **Lokal zuerst.** Der Rechner muss ohne Netz und ohne Cloud-Laufwerk weiterlaufen. Cloud-Ordner sind Ziele, keine Voraussetzung. Die Arbeitskopie des Maschinengedächtnisses liegt immer lokal unter `~/.config/ai-agents`.
5. **Keine neuen Abhängigkeiten.** Nur, was Omarchy mitbringt, plus python3 mit PyYAML, jq und rsync. Lieber ein bewährtes Muster übernehmen als ein neues Werkzeug installieren.
6. **Keine Geheimnisse.** Passwörter, Tokens und Schlüssel gehören in keine Datei, die dieses System schreibt, auch nicht ins Gedächtnis. Logins richtet der Nutzer selbst ein.
7. **Dokumentationspflicht.** Jede bleibende Änderung am Rechner im selben Arbeitsgang ins Maschinengedächtnis schreiben (Regeln in `~/.config/ai-agents/AGENTS.md`). Danach `python3 ~/.config/ai-agents/tools/memlint.py` ausführen, Exit 0 heißt in Ordnung.
8. **Entscheidungen des Nutzers respektieren.** Baseline setzen, Daten löschen und Zugriffsrechte lockern entscheidet der Nutzer. Im Zweifel fragen statt raten.

## Aufbau

```
Firefox-Erweiterung (ChatGPT, Claude) --Native Messaging--> Bridge (polaris)
   --> <Projektordner>/<Projekt>/raw/<quelle>/        Rohdaten, nur anhängen
Curator (polaris-curator, Timer nachts)
   --> <Projekt>/knowledge.md, decisions.md, open-loops.md, dokumente-index.md
Health Check (Widget + `polaris-curator health`)      liest nur, ändert nichts
omarchy-backup                                        Snapshots der Konfiguration
Maschinengedächtnis ~/.config/ai-agents                Regeln + Notizen aller Agenten
   --> optional einseitig gespiegelt (polaris-memory-mirror) in einen Projektordner
```

| Was | Wo |
|---|---|
| Programm (aus Paket) | `~/.local/share/polaris/app/`; aus git: der Checkout |
| Befehle | `~/.local/bin/polaris`, `polaris-curator`, `polaris-memory-mirror`, `omarchy-backup` |
| Konfiguration | `~/.config/polaris/config.yaml` (Projektwurzeln, `memory_mirror`), `curator.yaml` (Modell), `projects.yaml` (Zuordnung) |
| Units (`systemctl --user`) | `polaris-bridge.{path,timer}`, `polaris-curator.timer`, `polaris-memory-mirror.timer`, `omarchy-backup-*.timer` |
| Protokolle | `~/.local/state/polaris/polaris.log` (Bridge), `<Projekt>/.polaris/curator.log`, `~/.local/state/polaris/install-*.log` |
| Widgets | Kopien unter `~/.config/omarchy/plugins/<id>/` |
| Maschinengedächtnis | `~/.config/ai-agents/` (Index `AI_HANDOFF.md`, Regeln `AGENTS.md`) |

## Fallstricke (aus dem Betrieb gelernt)

- **Nie etwas unter einem nicht eingehängten Laufwerk anlegen.** Liegt der Projektordner auf einem Cloud-Laufwerk (z. B. rclone), ist der Mountpunkt ohne Laufwerk ein leerer lokaler Ordner. Dort Geschriebenes verdeckt später das Laufwerk oder verhindert das Einhängen. Bridge, Curator und Spiegel prüfen deshalb, ob der Projektordner existiert, und legen ihn nie an.
- **Jobs auf dem Laufwerk** brauchen in der Unit `Wants=`/`After=` des Mount-Dienstes. „Ordner nicht erreichbar“ ist ein vorübergehender Zustand (Exit 75), kein Grund zum Neuanlegen.
- **Dateidatum auf Cloud-Laufwerken ist kein Beleg.** Ob der Curator geschrieben hat, zeigen `curator.log` (`Lauf beendet …`, `knowledge_changed=True`) und die Einträge selbst (`Gültig seit`, `polaris-source`-Kommentare). Neue Einträge stehen in der passenden Rubrik, nicht am Dateiende.
- **Hintergrundprüfungen sparsam halten.** Was das Widget oder ein Timer häufig aufruft, darf keine Inhalte auf einem Cloud-Laufwerk lesen. Sonst erzeugt rclone regelmäßige Lastspitzen, die Lüfter hörbar anspringen lassen. `status-all` rechnet deshalb nur bei geändertem Fingerabdruck neu (`~/.local/state/polaris/status-cache.json`, `--no-cache` umgeht ihn), und das Widget aktualisiert alle 10 min sowie beim Öffnen.
- **Widgets** wirken erst nach `omarchy restart shell`. Vorher prüfen, dass kein aus dem Widget gestarteter Lauf aktiv ist. Omarchy lehnt symlinkte Plugin-Ordner ab, deshalb sind es Kopien. Eine frisch kopierte Kopie kennt die laufende Shell erst nach `omarchy-shell shell rescanPlugins`; erst danach geht `omarchy plugin enable`. Das Neueinlesen kann eingebaute Bereiche doppelt laden, deshalb startet `install.sh` die Leiste am Ende neu (`--no-restart` verhindert das).
- **`~/.mozilla` nie löschen und nie ein `~/.mozilla/firefox` anlegen.** Das Firefox-Profil liegt bei Omarchy unter `~/.config/mozilla/firefox/`, ein aktuelles Firefox legt `~/.mozilla` gar nicht an. `~/.mozilla` enthält nur die Native-Messaging-Registrierungen; `install.sh` legt dafür nur `~/.mozilla/native-messaging-hosts/` an, sobald ein Firefox-Profil existiert. Firefox also einmal starten, bevor `install.sh` den Native Host einträgt.
- **Tests nie gegen das echte Home.** Curator-Tests mit `POLARIS_NO_NOTIFY=1`, Bridge-Tests leiten `HOME` selbst um. Installer-Tests in einem leeren `HOME` mit Platzhaltern für `systemctl` und `omarchy` vorne im `PATH`.
- **Laufende Skripte nicht ändern.** bash liest Skripte stückweise. Eine Änderung während des Laufs führt zu Fehlern wie „unexpected EOF“.
- **Prozesse nie per Muster beenden** (`pkill -f …`), sondern über die genaue PID. Ein Muster trifft leicht fremde Prozesse.
- **git-Anmeldung über gh**, wenn gh per mise installiert ist: Als Credential-Helper den stabilen Wrapper `~/.local/bin/gh` eintragen, nicht den versionsgebundenen Pfad aus `gh auth setup-git`. Der zeigt nach jedem gh-Update ins Leere.
- **Gedächtnis-Spiegel:** Ist der Spiegel neuer als die lokale Fassung (typisch nach einer Wiederherstellung), überschreibt `polaris-memory-mirror` nichts und meldet es. Erst zurückholen: `rsync -rlt --exclude=.alt/ <Spiegel>/ ~/.config/ai-agents/`.
- **omarchy-backup:** Status YELLOW heißt nur „Abweichung von der Baseline“, kein Fehler. Eine Baseline setzt nur der Nutzer. Systemdateien unter `/etc` sichert es nicht; eigene Anpassungen dort gehören wörtlich ins Maschinengedächtnis.

## Installation abgebrochen: so geht es weiter

1. Protokoll lesen: `ls -t ~/.local/state/polaris/install-*.log | head -1`. Die letzte Zeile `== <Schritt>` vor dem Abbruch ist der fehlgeschlagene Schritt, die Zeilen `INSTALL: …` am Ende nennen Ergebnis und Exit-Code.
2. Ursache beheben. Häufige Fälle:

| Meldung | Ursache und Abhilfe |
|---|---|
| `PyYAML fehlt` | `sudo pacman -S python-yaml` (Passwort gibt der Nutzer ein) |
| `jq fehlt`, `rsync fehlt` | `sudo pacman -S jq` bzw. `rsync` |
| `Firefox-Profil fehlt` (früher `~/.mozilla fehlt`) | Firefox einmal starten und schließen, dann erneut installieren |
| `plugin '…' is not known` | ältere Paketfassung (vor 2026-10-15): `omarchy-shell shell rescanPlugins`, erneut installieren, danach `omarchy restart shell` |
| `omarchy-Befehl fehlt` | kein Omarchy oder nicht im `PATH`; Widgets mit `--no-widget` auslassen |
| `Modell-CLI fehlt` | `codex` bzw. `claude` installieren und anmelden (macht der Nutzer), dann `polaris-curator llm-check --call` |
| `Ziel … gibt es nicht` | Cloud-Laufwerk nicht eingehängt; einhängen oder Ziel später setzen |
| `kein Projektordner eingetragen` | `polaris roots add <Ordner>` |

3. `install.sh --dry-run` aus demselben Ordner ausführen und prüfen, was er tun würde. Danach `install.sh --yes` mit denselben Optionen wie im Protokoll, Zeile `INSTALL: Optionen`.
4. Prüfen:
   - `polaris-curator health`: Gruppen OK, gelbe und rote Zeilen nennen die Abhilfe.
   - `omarchy-backup status` und `omarchy-backup doctor`, falls eingerichtet.
   - `python3 ~/.config/ai-agents/tools/memlint.py`
5. Das Ergebnis in `~/.config/ai-agents/handoff/polaris.md` eintragen: Stand, was behoben wurde, was offen bleibt.

## Diagnose ohne Installer

- `polaris status`, `polaris roots`, `polaris pending`: Bridge, Projektordner, nicht zugeordnete Chats
- `polaris-curator health [--all]`: alle Einzelprüfungen
- `polaris-curator llm-check [--call]`: Modell und Anmeldung
- `systemctl --user list-timers 'polaris-*' 'omarchy-backup-*'`
- `journalctl --user -u polaris-curator.service --since today`
- Ausführliche Bedienung: `README.md` und `curator/README.md` im Programmordner, `extras/omarchy-backup/README.md`.
