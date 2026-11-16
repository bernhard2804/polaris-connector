# Änderungen

Was sich je Stand für Nutzer ändert. Die Versionsnummer ist die der
Firefox-Erweiterung (`extension/manifest.json`), unter ihr ändern sich auch
Bridge, Curator, Installer und Werkzeuge. Hier stehen vor allem Dinge, die
`install.sh` bzw. `polaris update` ungefragt mitändern oder die eine Nacharbeit
verlangen. Einzelheiten stehen in der README.

## Unveröffentlicht

- **Radar: hängendes Plugin-Repo bricht den Lauf nicht mehr ab** (2026-11-16,
  Beitrag von bernhard2804): Lief `git ls-remote` eines Plugins in die
  30-s-Zeitgrenze, beendete `subprocess.TimeoutExpired` den ganzen Radar
  (Exit 1, Health „systemd-Units fehlgeschlagen“, keine Sicherheitsprüfung an
  dem Tag). `git()` fängt das jetzt wie die übrigen Aufrufe ab; das Plugin
  steht dann als „Upstream nicht erreichbar“ in der Liste. Test in
  `memory/tests/test_radar.py`.

- **Radar: Beiträge in eigenen Repos** (2026-11-14): Mit `own_repos`
  (`owner/repo` oder `owner/*` für alle öffentlichen Repos eines Kontos außer
  Forks) listet der Radar offene Issues und PRs anderer im neuen Abschnitt
  „Eigene Repos“ und meldet neue per Desktop-Hinweis. Anlass: ein PR, der nur
  durch Nachfragen auffiel. Titel und Namen sind Fremdtext (entschärft, Links
  selbst gebaut). Je Repo eine Abfrage im bestehenden Tageslauf nach den
  übrigen Abschnitten, bei Rate-Limit Abbruch mit Hinweis; Obergrenzen (20
  Repos, 30 Einträge je Repo) werden genannt. Ohne `own_repos` ändert sich
  nichts. Dabei gehärtet (Fable): Die Entschärfung von Fremdtext entfernt
  verschachtelte HTML-Kommentare jetzt vollständig (vorher ergab
  `<!-<!---` wieder `<!--`, ein Fremder hätte so ein mem-Tag einschleusen
  können) und streicht Steuer-, Nullbreite- und Bidi-Zeichen.
- **Widget 0.3.5, Beschreibung und Autor** (2026-11-14): Autor und Copyright
  `mstio` statt Klarname (Widget-Manifest, beide `LICENSE`). Manifest, README
  und Widget-README stellen den Nutzen voran: eine belegte Erinnerung an das,
  was man mit KI erarbeitet hat, die einem selbst gehört (Fakten getrennt von
  Aussagen und Vermutungen, Markdown für jeden Agenten), statt „project
  memory“. `memory/README.md`: Radar-Timer läuft auch bei gesperrtem
  Bildschirm und wartet nach dem Aufwachen auf das Netz (Doku war veraltet,
  Befund der Fable-Prüfung zu PR #4).
- **Radar nach dem Aufwachen** (2026-11-13, Beitrag von bernhard2804, PR #4):
  Der Timer holt den verpassten Tageslauf beim Aufwachen sofort nach, bevor
  das Netz verbunden ist. Der Radar fand dann
  keine Quelle (Exit 75) und schrieb tagelang nichts (Health gelb „zuletzt
  geprüft vor 4 Tagen“). `polaris-radar.service` wartet jetzt wie der Curator
  bis zu 2 Minuten auf das Netz (`nm-online`, nicht blockierend).
  `polaris update` erneuert die Unit; nichts nachzuarbeiten.
- **Vier stille Schutzlücken geschlossen** (Gremium 2026-11-11, Fable und
  Codex mit Perspektivwechsel):
  - Lief der Curator ohne Sandbox (z. B. `POLARIS_NO_SANDBOX=1` nur in der
    Umgebung des Timers), ist die Health-Zeile Modell jetzt rot; vorher blieb
    sie grün.
  - Die Codex-Sandbox gibt nur noch den eigenen Programmordner frei; lag
    `codex` in `~/.local/bin`, `~/.cargo/bin` oder `~/bin`, war bisher z. B.
    ganz `~/.local` (mit allen erfassten Chats) lesbar. Hier (mise) unverändert.
  - Der unabhängige Prüfer sieht jetzt auch Statuswechsel von Entscheidungen
    und geschlossene offene Punkte; Einwände stehen als „Prüfhinweis“ daran.
  - Guard-Hook: Polaris' eigene Fremdtext-Ablagen (Quarantäne, verarbeitete
    und abgelehnte Chats, Radar) gelten als Fremdinhalt; Änderungen an
    `curator.yaml`, `projects.yaml`, Köder, Polaris-Units,
    `POLARIS_NO_SANDBOX` und Omarchys Standardagent fragen nach; eine
    unlesbare `config.yaml` schlägt geschlossen fehl.
  - Nachgebessert nach der Zweitmeinung (Fable): Der Vermerk „ohne Sandbox“
    bleibt sieben Tage stehen, auch wenn danach mit Sandbox gelaufen wird,
    und entsteht bei jedem Weg ohne Sandbox (auch `backfill`, `compact` …).
    `bwrap` kommt fest aus `/usr/bin`, ein Wirkungstest prüft, dass das Home
    in der Sandbox unsichtbar ist (ein falsches `bwrap` im PATH galt sonst als
    Sandbox). Die Codex-Freigabe bleibt auch bei pnpm und bei einem Home hinter
    einem Symlink eng. Der Guard-Hook fragt zusätzlich bei Änderungen an
    `environment.d`, an Units (auch `stop`/`kill`), am Live-Programmordner und
    an `bwrap` nach und wertet `polaris-radar`, die Quarantäne-Anzeige und
    rekursives Durchsuchen von Elternordnern als Fremdinhalt; dafür weniger
    Fehlalarme (z. B. `grep POLARIS_NO_SANDBOX` im Repo). Ein behobener Lauf
    ohne Sandbox bleibt sieben Tage gelb sichtbar. Grenze: Der Guard-Hook ist
    eine Textprüfung; Umwege über relative Pfade oder Skripte erkennt er nicht.
  - Wer `codex` über pnpm installiert hat: Der Shim bekommt in der Sandbox
    nur noch sich selbst und findet sein `node_modules` nicht; der Curator
    scheitert dann laut. Abhilfe: `codex` über mise oder npm installieren.
  Nacharbeit: aktiven Guard-Hook übernehmen
  (`POLARIS_ADOPT_GUARD=1 ~/.local/share/polaris/app/install.sh --yes --only-existing`).

- **Quick-Start mit Signaturprüfung** (`docs/install.md`): vor `./install.sh`
  Fingerabdruck und Signatur des Clones prüfen; Grenze der Erstinstallation
  (Vertrauen beim ersten Mal) beschrieben. Nur Doku, Nacharbeit: keine.

- **Signierte Updates** (nach dem Vorschlag eines Nutzers): `polaris update`
  übernimmt einen neuen Stand nur, wenn dessen neuester Commit mit einem
  Schlüssel aus `~/.config/polaris/allowed_signers` signiert ist; sonst bricht
  es ab, auch mit `--yes`. `install.sh` legt diese Datei einmalig aus
  `keys/allowed_signers` an und ersetzt sie nie. **Nacharbeit, einmal vor dem
  Update:** Dieses Update übernimmt das alte `update.sh` noch ungeprüft. Den
  Fingerabdruck über einen zweiten Kanal abgleichen und die Signatur von Hand
  prüfen (`docs/install.md`, „Signed updates“, „Existing installs, once“), erst
  dann `polaris update`. Ab dem nächsten Update prüft Polaris selbst.

- Curator und Health Check folgen ohne ausdrückliche Vorgabe dem Omarchy-
  Standardagenten. `runtime` in `curator.yaml` steuert nur den Curator;
  `health_agent` überschreibt nur den Knopf „Agent fragen“. Vorhandene
  `curator.yaml` bleibt unangetastet. Alte Konfigurationen ohne `runtime`
  bleiben aus Datenschutzgründen bei Codex; `runtime: default` aktiviert die
  dynamische Auswahl. Ein bewusster
  Wechsel des Omarchy-Standardagenten kann den Anbieter für Curator-Daten und
  Health-Check-Meldungen ändern. Die Curator-Pipeline unterstützt als
  Omarchy-Standardagent derzeit Codex und Claude; andere Auswahl wird als
  Fehler gemeldet. Für einen anderen Curator-Agenten gibt es `runtime: command`
  mit eigener Anbindung. Wird Claude zum Standard, entfallen Bilder aus
  Mail-Anhängen bei der Kuration; der Text bleibt. Kein zusätzlicher
  Hintergrundprozess.

## 0.4.6 (2026-11-10)

- **Erweiterung 0.4.6** (Auto-Update über `update_url`):
  - Nach einem Fehler meldet sie den nächsten erfolgreichen Abruf auch dann,
    wenn sich der Chat nicht geändert hat. Eine Warnung zur ChatGPT-Schnittstelle
    bleibt so nicht mehr stehen, obwohl alles wieder geht.
  - Als vorübergehend (warten statt aus der Seite lesen) gelten nur noch
    HTTP 429/5xx und Netzfehler, nicht mehr jeder `TypeError`. Ein
    Programmierfehler oder ein verändertes Antwortformat fällt auf die Seite
    zurück. Gleiche Regel wie im Health Check.
- **Widget 0.3.4:** Ein alter, lesbarer Status-Cache (vor 0.3.3) wird jetzt
  bei jedem Aufruf auf `0600`/`0700` gesetzt, auch wenn der Bildschirm gesperrt
  ist, die CLI fehlt oder der Status nicht gelesen werden konnte; bisher
  geschah das erst nach einem erfolgreichen Lauf. Betrifft vor allem
  Widget-Installationen ohne Connector mit eigenem `XDG_STATE_HOME`. Nach dem
  Hinweis von HANCORE-linux im Omarchy-Plugin-Marktplatz (#13877).
- Doku: Zweck und bewusste Grenzen des Sicherheitsabgleichs in `docs/radar.md`
  („Scope“): Aufmerksamkeitshilfe, keine Security-Suite.
- **Radar nach dem Prüfgremium:** Eine Quittierung gilt nur, solange der
  Befund gleich lautet; ändert sich die Bewertung (NVD-Urteil, Rückstand,
  Schweregrad), ist er wieder offen. Fällt eine der drei Quellen aus, zeigt
  der Health Check „Abgleich unvollständig“ statt „keine Befunde“. Untergrenzen
  wie „> 10.6“ schließen `10.6p1` jetzt ein.
- **Radar: OpenSSH-Versionen richtig eingeordnet.** `10.6p2` galt bisher als
  älter als `10.6` und blieb so nach dem Fix „betroffen“. Für openssh,
  sudo und ntp (Positivliste) gilt jetzt die CPE-Logik, auch für die Arch-Schreibweise `1.9.17.p2`.
- **Guard-Hook schützt `~/.config/polaris/radar.yaml`** (Quittierungen blenden
  Sicherheitsbefunde aus). Nacharbeit bei aktivem Hook: `POLARIS_ADOPT_GUARD=1
  install.sh --yes --only-existing` im Programmordner (übernimmt die neue
  Hook-Fassung; macht der Nutzer).
- **Sicherheitsbefunde quittieren:** Ein geprüfter, hier nicht zutreffender
  Befund des Radars lässt sich in `~/.config/polaris/radar.yaml` unter
  `security.acknowledged` quittieren (`id`, `subject` = installierter Stand,
  `until` höchstens 90 Tage, `reason`; Beispiel in `radar.example.yaml`). Er
  bleibt im Radar sichtbar, die Health-Check-Zeile wird grün („n quittiert“).
  Nach einem Update des Pakets bzw. Kernels oder nach Ablauf ist er wieder da.
- **Weniger Fehlalarm bei gedrosselter ChatGPT-Schnittstelle:** Ein
  vorübergehender Fehler (HTTP 429, 5xx, Netz) erscheint im Health Check nur
  noch 30 Minuten lang und mit zutreffendem Text („die Erweiterung wiederholt
  den Abruf selbst“). Die Erweiterung liest in diesem Fall nicht aus der Seite,
  sondern wartet und ruft erneut ab. Solange ein synchronisierter Chat offen ist,
  erneuert jeder weitere Fehlversuch die Meldung. Andere Fehler (auch Ablehnungen
  durch die Bridge und Programmierfehler der Erweiterung wie „TypeError: …“
  außer Netzfehlern) bleiben wie bisher drei Stunden sichtbar.

## Zweiter Stand 2026-11-09 (Bridge, Curator, Installer; Erweiterung bleibt 0.4.5)

- **Nacharbeit vor dem Update: bubblewrap installieren** (`sudo pacman -S
  bubblewrap`, auf Omarchy meist schon vorhanden: `command -v bwrap`). Polaris
  verarbeitet Fremdinhalte seit diesem Stand nur noch in der Sandbox
  (fail-closed). Ohne nutzbares bubblewrap ist der Health Check rot und
  `polaris update` nimmt das Update automatisch zurück. `install.sh` warnt
  jetzt bei fehlendem `bwrap`.
- `polaris update` warnt, wenn der Health Check vor und nach dem Update nicht
  lesbar ist (Update dann ungeprüft).
- Anleitung „Switching from the install bundle to a git checkout“: legt
  vorher eine vollständige Kopie des Programmordners an.

- **Private Ordner nur für den eigenen Benutzer (2026-11-08):** Polaris legt
  `~/.config/polaris`, `~/.local/share/polaris` (erfasste Chats) und
  `~/.local/state/polaris` mit `0700` an und zieht bestehende Ordner beim
  nächsten `polaris update` nach, ebenso einen vorhandenen
  `~/Downloads/polaris-inbox` (Symlinks bleiben unangetastet, mit Warnung). Das
  Widget (0.3.3) legt seinen Zwischenspeicher und `manual-run.json` nur für den
  eigenen Benutzer an. Schützt Chat-Titel und -Inhalte, falls das Home für andere Konten
  durchquerbar ist. Nach dem Hinweis von HANCORE-linux im
  Omarchy-Plugin-Marktplatz (#13877). Nacharbeit: keine.

- **Sicherheitsabgleich lief nicht (behoben 2026-11-08):** Der tägliche
  Radar-Lauf startet nachts und wurde bei gesperrtem Bildschirm jedes Mal
  übersprungen (Bedingung `polaris-awake`, gedacht für Zugriffe aufs
  Cloud-Laufwerk, die der Radar nicht macht). Der Health Check meldete es nach
  drei Tagen gelb. Nacharbeit: keine (`polaris update` tauscht die Unit).

- **Alarmmüdigkeit messen (2026-11-08):** Der Nutzungszähler hält je
  Health-Prüfung die Tage mit Warnung oder Fehler fest (`health-warn:<id>`,
  `health-error:<id>`, Nenner `health:day`; nur Prüf-IDs, höchstens ein
  Schreibvorgang je Tag). Grundlage, um am 05.12. Zeilen zu schärfen oder zu
  streichen, die lange gelb sind, ohne dass jemand handelt.

- **Befunde der Gutachten von Codex und agy umgesetzt (2026-11-07):**
  `polaris update` zieht omarchy-backup und git-verwaltete Widgets nicht mehr
  ungesehen nach, sondern zeigt neue Commits; übernommen wird nach Zustimmung
  im Terminal oder mit `POLARIS_UPDATE_EXTRAS=1 polaris update`. Ein aktiver
  Guard-Hook wird nur mit `POLARIS_ADOPT_GUARD=1` ersetzt (sonst Unterschied
  und Hinweis). Eine aus der Sandbox erneuerte Anmeldung darf nur Tokens und
  Zeitstempel ändern (Positivliste); eine abgelehnte Übernahme zeigt der
  Health Check gelb. Update-Prüfung ab 3 Tagen ohne Erfolg gelb. Der
  Ende-zu-Ende-Test läuft zusätzlich in der echten Sandbox.
  Nacharbeit: bei neuen Widget- oder omarchy-backup-Ständen einmal
  `POLARIS_UPDATE_EXTRAS=1 polaris update` oder `polaris update` im Terminal.
- **Grenzen für Fremdinhalt (ADR 004, Schritt 0, 2026-11-07):** Office-Dateien
  werden nicht entpackt, wenn sie mehr als 256 MB (ein Teil mehr als 32 MB) oder
  über 10 000 Teile ergäben (Zip-Bombe); einzelne Dateien und Anhänge über 50 MB,
  Mails über 100 MB bleiben liegen. Der Health Check meldet verwaiste
  Zwischendateien des Curators (älter als ein Tag) gelb. Nacharbeit: keine.
- **Nahtstellen-Tests (ADR 003, 2026-11-07):** Beispieldateien für das
  Bridge-Ereignis (`tests/contracts/`, ChatGPT und Claude) laufen durch die
  echte Bridge und werden mit dem echten Curator gelesen; ein Ende-zu-Ende-Test
  geht über die echten Befehle (`polaris process`, `polaris-curator run`) mit
  Fake-Modell bis `knowledge.md`, samt Störfall „Projektordner weg“.
- **Für Agenten (ADR 003, 2026-11-07):** `AGENTS.md` im Repo (auch als
  `CLAUDE.md`) mit Arbeitsablauf, „Wo ändere ich was?“ und der Liste der
  Schutzdateien (Zweitmeinungspflicht); `tests/test_boundaries.py` prüft die
  Importrichtung (Bridge und Curator importieren einander nie, der Kern weder
  Health Check noch CLI) und die Liste gegen das Repo. Nacharbeit: keine.
- **Schutz geschlossen (ADR 003, 2026-11-07):** Ohne nutzbares bubblewrap
  verarbeitet Polaris keinen Fremdinhalt mehr (vorher lief es ungeschützt
  weiter): Kuratierung und PDF-Auslesen setzen aus, der Health Check zeigt rot
  „Sandbox nicht nutzbar – Kuratierung ausgesetzt“; bewusst ohne Sandbox nur mit
  `POLARIS_NO_SANDBOX=1`. Eine aus der Sandbox erneuerte Anmeldung wird nur
  zurückgeschrieben, wenn Form, Konto, Modus und Rechte gleich bleiben.
  Zwischendateien des Curators haben eindeutige Namen. `install.sh` zeigt den
  Unterschied und ersetzt einen aktiven Guard-Hook nur mit `POLARIS_ADOPT_GUARD=1`.
  Nacharbeit: keine (bubblewrap bringt Omarchy mit).
- **Health Check entlastet (ADR 003, 2026-11-07):** Projekttrennung und
  Maschinengedächtnis rechnen nur neu, wenn sich ihre Eingaben ändern
  (Verzeichnislisten bzw. Datei-Metadaten, keine Inhalte; spätestens nach 6 h);
  `~/.local/state/polaris/health-cache.json`. Vorher las jeder Widget-Aufruf
  (alle 10 min) alle Rohdaten-JSONs vom Projektlaufwerk und startete memlint;
  `health` jetzt ~0,45 s statt ~1,7 s. Spiegelzustand und vorübergehende Fehler
  werden nie zwischengespeichert. Die tägliche Update-Prüfung läuft losgelöst im
  Hintergrund (Health wartet nicht mehr bis zu 70 s auf das Netz; ohne Netz
  höchstens ein Versuch je Stunde, `update-check.attempt`); ein lange nicht
  geprüfter Stand wird angezeigt (ab 3 Tagen gelb). Nacharbeit: keine.
- **Nutzungszähler vollständig (ADR 003, 2026-11-07):** zählt jetzt auch
  Bridge-Befehle (`bridge:cli:*`), Native-Host-Anfragen und archivierte Quellen
  (`bridge:native:*`, `bridge:archive:*`), kuratierte Quellen und Dokumenttypen
  (`source:*`, `doc:*`), Modell-Runtime und Ersatzmodelle (`llm:*`),
  Journal-Wiederherstellung und Dokumentenverzeichnis. Widget-Aufrufe stehen
  getrennt unter `widget:*` (bisher als `cli:health`/`cli:status-all`
  mitgezählt). Funktionen zählen höchstens einmal je Lauf; Dateisperre gegen
  verlorene Zählungen. Nur Namen und Anzahl, nie Inhalte. Nacharbeit: keine.
- **`polaris update` sicherer (ADR 003, 2026-11-07):** nimmt ein Update auch
  zurück, wenn vorher schon eine andere Prüfung rot war (Vergleich je Prüfung
  statt Gesamtstatus); tauscht keinen Code, solange Curator oder Bridge laufen
  (Exit 75, später erneut); die Wiederherstellung schreibt ein Protokoll
  `~/.local/state/polaris/update-<Zeit>.log`. Tests neu.
- **`install.sh` brach bei langen Update-Hinweisen ab** (mehr als 40 Zeilen in
  `neu-seit-update.md`): die Gedächtnis-Werkzeuge und die Abschlussprüfung
  liefen dann nicht. Behoben, Test neu. Nacharbeit: keine (`polaris update`
  holt es nach).
- **Live-Stand als git worktree** möglich (`.git` ist dort eine Datei);
  ein worktree-Live-Stand landet nicht zusätzlich im omarchy-backup.
  Läuft Live als worktree, verweigern `install.sh` und `update.sh` im
  Entwicklungs-Checkout den Lauf (sie würden Live still auf den
  Entwicklungsstand biegen; bewusst: `POLARIS_INSTALL_HERE=1`). Die Sperre vor
  einem Update deckt auch Gedächtnis-Spiegel und Radar ab und greift direkt
  vor dem Wechsel.
- **Rückmeldungen von bernhard2804 (2026-11-05):**
  - `polaris update`: Der Health Check am Ende meldete noch „Update
    verfügbar“; der Zustand wird jetzt vorher geschrieben (Test neu).
  - `home-snapshots-einrichten.sh`: alle root-Schritte in einem Aufruf (eine
    Passwortabfrage), neu `--pkexec` statt sudo.
  - Hinweis zur AGENTS-Vorlage: nur einzelne neue Regeln übernehmen, nie die
    ganze Datei (`install.sh`, Agenten-Leitfaden).
  - `docs/health-check.md`: wann „Neustart“, „Backup-Ziel“ und
    „Plattenfehler“ als Zeile erscheinen.

- **READMEs auf Englisch (2026-11-05):** `curator/README.md`, `memory/README.md`
  und `dist/README.md` übersetzt und gegen den Code abgeglichen (u. a.
  vollständige CLI, Module, Health-Check-Gruppen, Sandbox, `replaces_id`,
  Modell `gpt-6-astra` in der Vorlage, erledigte „Bekannte Grenzen“).
  Beispielnamen in der Curator-README neutralisiert.

- **Widget 0.3.2: „Agent fragen“ (2026-11-05):** Bei jeder auffälligen
  Health-Check-Zeile öffnet „Agent fragen“ den Standard-Agenten (Laufzeit
  des Curators) mit der Meldung und, wo bekannt, einem passenden Befehl als
  Vorschlag; er prüft und schlägt vor, ändert aber erst nach Zustimmung
  etwas. Neu:
  `polaris-curator health --ask <id>`. Meldung „Repos“ nennt Pfad und Datei.
- **Health Check: Hinweise auf Übersehenes (2026-11-05):** neue Teile
  „Backup-Ziel“ (Befunde der Zielprüfung von omarchy-backup sofort, nicht
  erst nach `doctor`), „Repos“ (neue, nicht versionierte Dateien in
  gesicherten Repos fehlen im Backup), „Neustart“ (Kernel aktualisiert) und
  „Plattenfehler“ (btrfs-Fehlerzähler). Lesen nur lokale Dateien.
- **Widget 0.3.1 (2026-11-05):** zeigt fremde Texte (Dateinamen, Radar,
  Fehlermeldungen der Modell-CLI) nur noch als reinen Text, nie als HTML
  (Härtung vom 04.11. jetzt auch im Marktplatz-Repo `mst.polaris-curator`).
- **Entschlackt (2026-11-05):** alte Migration der Curator-Zustände aus
  September (`legacy_migration`, Feld `coverage_migrated`), Journale am alten
  Ort auf dem Laufwerk (bis 10.10.), toter Code (`guard.strip_invisible`,
  `ids.ID_RE`) und die Machbarkeitsstudie `experiments/agent-runtime-poc`
  (archiviert im Tag `archiv/agent-runtime-poc-20261105`) entfernt.
  Wer Polaris vor dem 10.10.2026 zuletzt laufen ließ: erst auf den Stand
  `3e89a3e` aktualisieren und einen Curator-Lauf abwarten.
  `scripts/publish-public.sh` startet jetzt alle Tests (`scripts/run-tests.sh`).
- **Nutzungszähler (2026-11-05):** Der Curator zählt lokal, wie oft welcher
  Befehl und welche Funktion (Verdichtung, Schleifenprüfung, Prüfer) läuft:
  `~/.local/state/polaris/usage.json`, nur Name, Anzahl, erstes/letztes Datum,
  nie Inhalte. Grundlage, um Ungenutztes mit Beleg zu entfernen.
- **Sicherheitswerkzeuge im Maschinengedächtnis (2026-11-05):** `install.sh`
  legt `claude-guard-hook.py`, `claude-guard-einschalten.sh` und
  `home-snapshots-einrichten.sh` nach `~/.config/ai-agents/tools/`, schaltet
  aber nichts ein (Hook für Claude Code gegen versteckte Anweisungen aus Mails
  und Webseiten; stündliche, für Agenten unerreichbare Snapshots von `/home`).
  Einzelheiten: `memory/README.md`.
- **Sicherheit (Prüfung vom 2026-11-04, Grundsatz „never trust your input“):**
  - `<Projekt>/.polaris/curator-config.yaml` darf nur noch `batching`, `budget`,
    `context`, `monitoring`, `compaction` und `attachments` setzen. Laufzeit,
    Befehl (`runtime: command`), Anbieter und Modelle kommen nur aus der lokalen
    `curator.yaml`. Vorher konnte jeder, der in einen Projektordner auf dem
    Cloud-Laufwerk schreiben kann, damit Befehle als Nutzer ausführen. Andere
    Schlüssel werden ignoriert und im Health Check gemeldet.
  - Gedächtnis-Spiegel: `install.sh` holt ihn nur noch mit
    `--memory-from-mirror` oder nach Rückfrage zurück, `tools/` nie. Der Health
    Check startet memlint aus dem Programm, nicht aus `~/.config/ai-agents`.
    memlint legt nur noch Einstiege unter `$HOME` auf Markdown-Dateien bzw.
    Skill-Ordner im Gedächtnis an.
  - codex (Curator) und pdftotext laufen in bubblewrap: kein Blick ins Home,
    geleerte Umgebung (keine Tokens aus der Shell), codex mit eigenem
    `CODEX_HOME`, das nur die Anmeldung enthält (Sitzungsprotokolle in
    `~/.codex` bleiben unsichtbar), pdftotext ohne Netz. Fehlt die Sandbox,
    meldet der Health Check das (Zeile „Modell“). Seit dem gleichen Tag auch
    Claude (Ersatz und Prüfer, eigenes `CLAUDE_CONFIG_DIR` nur mit der
    Anmeldung) und `runtime: command` (eigenes leeres Home; nötige Pfade per
    `command_sandbox_ro`/`command_sandbox_rw` in der lokalen `curator.yaml`).
  - Projekt-Overrides dürfen Läufe nur billiger oder seltener machen, nie
    teurer; unlesbare Werte werden gemeldet statt den Lauf abzubrechen.
  - Spiegel: Links, die aus dem Gedächtnis hinauszeigen, kommen nicht mit;
    memlint meldet solche Links rot (`symlinks`). Wurde ein vorhandener
    Spiegel bei der Installation nicht zurückgeholt, sperrt `.mirror-hold` das
    Spiegeln, bis entschieden ist.
  - Radar: fremde Links, Kennungen, Daten und Versionen werden streng geprüft;
    Benachrichtigungen maskieren Markup, das Widget zeigt Fremdtext nur als
    reinen Text.
- Ausgabeprüfung: Modellantworten mit geheimnisähnlichem Text, dem lokalen
  Köder (`~/.config/polaris/canary`, wird beim ersten Lauf angelegt) oder
  Anweisungen an eine KI bzw. Shell-Befehlen werden nicht geschrieben, sondern
  lokal in Quarantäne gelegt (`polaris-curator quarantine list|show|release|drop`);
  der Health Check meldet sie. Die Prüfung normalisiert Unicode-Tricks
  (Nullbreite, ähnliche Buchstaben, Breitformen), gilt auch für Verdichtung,
  Neu-Einordnung, Aufräumen offener Punkte und den Dokumentindex; der Prompt
  verbietet an Software gerichtete Aufforderungen, Befehle und Zugangsdaten
  und erfasst angebliche Änderungen an Zahlungen/Bankverbindungen von außen
  als „Verifizieren“-Punkt.
- Agentenleitfaden: Updates erst nach Blick des Nutzers auf die neuen Commits
  einspielen; Spiegel nie blind zurückholen.
- Das Widget gibt es auch als eigenes Plugin-Repo `mstio/mst.polaris-curator`
  (für den Omarchy-Plugin-Marktplatz), Anzeigename jetzt „Polaris“. Ein so
  installiertes Widget (git-Checkout) lässt `install.sh` in Ruhe,
  `polaris update` aktualisiert es per `omarchy plugin update`, der Health Check
  vergleicht es nicht mehr mit dem Quellordner.
- Widget und Health Check sprechen Englisch oder Deutsch: `language: en|de` in
  `~/.config/polaris/curator.yaml`. Ohne die Zeile bleibt alles deutsch,
  bestehende Installationen ändern sich also nicht. Neue Installationen
  bekommen `language` aus `install.sh --language` bzw. der Systemsprache
  (`LANG=de_*` → `de`, sonst `en`). Die Feldbezeichnungen in `knowledge.md`
  bleiben vorerst deutsch.
- Polaris wird nur noch per git verteilt; das Installationspaket (`make-bundle.sh`,
  `dist/polaris-setup-*`) entfällt. `install.sh` holt die Begleit-Repos selbst:
  Backup-Widget, Agenten-Starter und Dock per `omarchy plugin add` (danach
  `omarchy plugin update`), die omarchy-backup-CLI per `git clone` nach
  `~/.local/share/polaris/extras/` – nur, wenn es noch keine eigene Installation
  gibt. `polaris update` zieht diese Teile mit und richtet nichts Neues ein
  (`install.sh --only-existing`; auf bestehenden Installationen fügt auch
  `install.sh --yes` keine neuen Widgets hinzu, sondern nennt den Befehl).
  Das Gedächtnis bekommt einen Platzhalter `handoff/omarchy-radar.md` samt
  Indexeintrag, auch ein bestehendes (sonst meldete memlint „fehlende Datei“
  bzw. „nicht im Index verlinkt“, weil der Radar sich nicht selbst einträgt).
  Ein vorhandenes, aber nie aktiviertes Widget wird jetzt aktiviert; Widgets,
  die noch als Kopie aus dem alten Paket laufen, nennen den Umstieg auf git.
  Gleichstand der Vorlagen prüft `scripts/check-sync.sh`. Nacharbeit: keine;
  Installationen aus dem alten Paket steigen einmal auf git um (README).

- README und CONTRIBUTING auf Englisch, die README nach Kern und Modulen gegliedert
  (Projektgedächtnis als Kern; Quellen, Modell-Runtimes, Widget, Health Check,
  Radar, Maschinengedächtnis als Module). Übrige Doku bleibt deutsch. Nacharbeit: keine.

- Neu: Versionsabgleich im Radar-Sicherheitsabschnitt. Aktiv ausgenutzte Lücken
  (CISA KEV) auf ein installiertes Paket werden mit den Versionsbereichen der NVD
  verglichen; ist die installierte Version nicht betroffen, bleibt der Health
  Check grün. Höchstens 5 NVD-Abfragen je Lauf, Zwischenspeicher
  `~/.local/state/polaris/nvd.json`. Nacharbeit: keine.

- Neu: geschützte Ordner für die Privatsphäre-Prüfung (Paket und öffentlicher
  Spiegel). Eine leere Datei `.polaris-protected` im Projektordner oder eine
  Zeile in `~/.config/polaris/protected.txt` sperrt alles aus dem Ordner:
  Namen (exakte Schreibweise), Pfad, Dateien byte-gleich, jede Inhaltszeile und
  5-Wort-Bruchstücke. Das allgemeine kleingeschriebene Wort bleibt erlaubt.
  Dazu ein `pre-push`-Hook im Spiegel-Klon, den `publish-public.sh` einrichtet;
  er prüft auch Autor und Nachricht der Commits, Spiegel-Commits tragen die
  GitHub-noreply-Adresse. Nacharbeit: keine.

- Neu: Sicherheitsabgleich im Omarchy-Radar. `polaris-radar` gleicht installierte
  Pakete mit dem Arch Security Tracker, den laufenden Kernel mit kernel.org-Stable
  und aktiv ausgenutzte Lücken (CISA KEV) mit Kernel und Paketen ab; Abschnitt
  „Sicherheit“ in `handoff/omarchy-radar.md`, Health-Check-Zeile „Sicherheit“.
  `install.sh` (also auch `polaris update`) richtet dafür den Timer
  `polaris-radar.timer` ein (täglich, ~3 MB); der Hook nach `omarchy update`
  startet jetzt diese Unit. Abschalten in `~/.config/polaris/radar.yaml`
  (`security: {enabled: false}`).

- Neu: `polaris-radar` (Omarchy-Radar). Nach jedem `omarchy update` (Hook in
  `~/.config/omarchy/hooks/post-update.d/`) und auf Anfrage schreibt es
  `handoff/omarchy-radar.md`: Plugin-Updates, vorgemerkte Issues, passende
  Omarchy-Neuerungen und neue Plugins. Agenten lesen es vor Omarchy-Anpassungen
  (neue allgemeine Regel). Stichworte optional in `~/.config/polaris/radar.yaml`
  (Vorlage `memory/tools/radar.example.yaml`). `install.sh` richtet Befehl und
  Hook ein (`--no-memory` lässt beides aus).

- Updates weisen auf Neues hin: `install.sh` (also auch `polaris update`)
  schreibt, was im CHANGELOG seit dem letzten Lauf neu ist, nach
  `~/.local/state/polaris/neu-seit-update.md` und als offenen Punkt nach
  `handoff/polaris.md`. Der Agent stellt es dem Nutzer vor; entschieden wird
  gemeinsam. Geänderte allgemeine Agentenregeln meldet er ebenso, bis der
  Abgleich quittiert ist.
- Allgemeine Agentenregeln (`memory/template/AGENTS.md`) neu und vollständiger:
  u. a. ressourcenschonend arbeiten, Sicherungskopien nie unter erratenem
  Namen, knapp schreiben, vor `git push` privat/öffentlich prüfen. Die Vorlage
  wird aus den als „allgemein“ gekennzeichneten Abschnitten erzeugt
  (`scripts/agents-template.py`). Ein vorhandenes `~/.config/ai-agents/AGENTS.md`
  ändert `install.sh` nicht; Neues bei Bedarf von Hand übernehmen.
- Der Health Check überwacht nur die in `curator.yaml` unter
  `monitoring.chat_sources` ausgewählten Browser-Chatquellen (`chatgpt`,
  `claude` oder keine). Die Curator-Runtime bleibt davon unabhängig.
  Bestehende Konfigurationen ohne diese Angabe behalten beide Quellen als
  Voreinstellung. Auf Systemen ohne unterstützte Browser-Chats `[]` setzen;
  dann prüft der Health Check weder Erweiterung noch Native Host.
- Bei Claude-only bleibt die Bridge-Fehlerprüfung aktiv. Eine vorhandene
  Claude-Nachricht verdeckt eine fehlende ChatGPT-Erfassung nicht mehr.
- Nennt `monitoring.chat_sources` Claude ausdrücklich, prüft der Health Check
  Claude so streng wie ChatGPT: noch nie eine Claude-Nachricht oder fehlender
  Zugriff auf claude.ai ist dann rot. Ohne die Angabe bleibt Claude wie bisher
  nachsichtig (neutral bzw. gelb).
- Groß-/Kleinschreibung in `chat_sources` ist egal. Unbekannte Einträge (z. B.
  `grok`) brechen nichts mehr ab, sondern erscheinen als rote Zeile
  „Chatquellen-Konfiguration“ im Health Check und als Warnung im Curator-Lauf.
- Ohne `chat_sources`-Angabe nennen die ChatGPT-Warnungen den Ausweg
  (`monitoring.chat_sources` setzen); der Agenten-Leitfaden beschreibt die
  Liste ebenfalls.
- Ältere Firefox-Erweiterungen vor 0.2.0 melden bei aktiver Claude-Quelle jetzt
  rot („unterstützt Claude-Erfassung noch nicht“); bitte aktualisieren.
- Der Installer fragt bei neuen Konfigurationen nach den Chatquellen
  (`--chat-sources` für unbeaufsichtigte Läufe). Ohne Chatquellen richtet er
  keinen Native Host und keine Bridge-Timer ein bzw. deaktiviert vorhandene
  Bridge-Timer beim erneuten Lauf.

## 0.4.5 (2026-11-09)

Teil-Release aus Zweig `release-0.4.5` (Spiegelstand 05.11. plus die
folgenden Punkte); alles unter „Unveröffentlicht“ ist noch nicht im Spiegel.

- **Erweiterung 0.4.5: „Letzter Export“ je Chat (2026-11-09):** Das Popup
  zeigte unter „Last export“ die Zeit des Projekts, also auch Exporte aus
  anderen Chats desselben Projekts; ein seit Tagen ruhender Chat wirkte so wie
  „vor Minuten synchronisiert“. Jetzt zwei Zeilen: „dieser Chat“ und „Projekt“,
  bei Exporten vor heute mit Datum. Bis zum ersten neuen Export nimmt die
  Chat-Zeile den Zeitpunkt aus der „schon exportiert“-Liste (nur
  Nachrichten-Exporte, bei ChatGPT-Abbildern „-“). Nacharbeit: keine,
  Firefox aktualisiert die Erweiterung selbst.

- **Privatsphäre-Prüfung:** fester Vorspann von `knowledge-archiv.md` gilt als
  Polaris-Formatzeile (Fehltreffer in geschützten Projekten mit Archiv).

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
