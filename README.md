# Polaris ChatGPT Connector

Archiviert ausgewaehlte ChatGPT- und Claude-Projekt-Unterhaltungen lokal,
Turn fuer Turn, als Rohdaten unter `<Projekt>/raw/chatgpt/` bzw.
`<Projekt>/raw/claude/`. Keine Zusammenfassung, keine KI, keine
Cloud-Infrastruktur - siehe Prinzipien im Auftrag. Aktuelle Extension-Version:
siehe `extension/manifest.json`.

Dieses Repo enthaelt zusaetzlich den **Polaris Curator** (`curator/`), der
auf diesen Rohdaten aufbaut und daraus kondensiertes Projektwissen
(`knowledge.md`, `decisions.md`, `open-loops.md`) erzeugt - siehe
`curator/README.md` fuer dessen eigene Dokumentation. Der Connector (dieses
Dokument) kennt keine KI und keine Curator-Logik; die Trennung ist bewusst.

Unter `omarchy-plugin/mst.polaris-curator/` liegt ausserdem das zugehoerige
theme-aware Omarchy-Bar-Widget. Es zeigt neue Chat- und Inbox-Quellen pro
Projekt, startet einzelne oder alle offenen Curator-Laeufe und oeffnet die
kuratierten Wissensdateien. Die Installation ist im Plugin-README beschrieben.

## Schnellstart auf einem neuen Omarchy-Rechner

```bash
git clone <repo> ~/Projects/polaris-chatgpt-connector
cd ~/Projects/polaris-chatgpt-connector
./install.sh                                # gefuehrt: fragt nach, Zusammenfassung, Bestaetigung
./install.sh --dry-run --projects ~/Projekte   # ohne Fragen: nur anzeigen
./install.sh --yes --projects ~/Projekte    # ohne Fragen: einrichten
polaris-curator llm-check --call            # Modell-CLI angemeldet? (kostet einen kleinen Aufruf)
```

`install.sh` ist idempotent (auf einem fertig eingerichteten Rechner ein
No-op) und installiert nichts aus dem Netz: Symlinks `~/.local/bin/polaris`
und `polaris-curator`, Bridge-Konfiguration und `project_roots`, eine
`curator.yaml` aus `curator/curator.example-{codex,claude}.yaml` (nur wenn
keine existiert; Runtime `codex`, wenn installiert, sonst `claude`, oder
`--runtime`), die systemd-Units (Bridge-Path/-Timer, Curator-Timer), den
Firefox-Native-Host und das Omarchy-Widget. Ersetzte Dateien sichert es nach
`~/.local/share/polaris/install-backups/<Zeit>/`. Voraussetzungen: `python3`
mit PyYAML (`python-yaml`), fuer das Widget `jq`, fuer den Curator `codex`
oder `claude` (angemeldet), fuer die Erweiterung Firefox. Die Erweiterung
selbst wird von Hand installiert (siehe „Extension installieren“).
Aus einem git-Checkout sind omarchy-backup und das Maschinengedaechtnis
(`~/.config/ai-agents`) optional; der Health Check zeigt sie dann als „nicht
eingerichtet“. Das Installationspaket bringt beide mit (siehe unten).

### Weitergabe ohne git (Installationspaket)

`./make-bundle.sh` baut `dist/polaris-setup-<Version>.tar.gz` mit dem ganzen
Polaris (Nutzervorgabe 2026-10-08: jede Aenderung an Polaris kommt ins Paket):

- Code, neueste signierte `.xpi` und `install.sh`; ohne Tests, Experimente und lokale Daten
- `extras/omarchy-backup/` und `extras/mst.omarchy-backup/`: CLI und Widget, jeweils der committete Stand der eigenen Repos (`OB_REPO`, `OB_PLUGIN`)
- `memory/`: `memlint.py` und eine leere Vorlage des Maschinengedaechtnisses, nie dessen Inhalte. Der Bau bricht ab, wenn `memory/tools/memlint.py` von `~/.config/ai-agents/tools/memlint.py` abweicht.

Vor dem Packen prueft `scripts/privacy-check.py` alle Dateien auf private Angaben:
den eigenen Home-Pfad, die git-E-Mail, die Namen der Projektordner unter den
Polaris-Wurzeln und Begriffe aus `~/.config/polaris/bundle-deny.txt`. Bei einem
Treffer wird nichts gebaut. Allgemeine Ordnernamen stehen in
`~/.config/polaris/bundle-allow.txt`; beide Listen bleiben lokal.

Der Empfaenger braucht weder git noch GitHub-Zugang:

```bash
unzip polaris-setup-<Version>.zip          # oder: tar -xzf polaris-setup-<Version>.tar.gz
bash polaris-setup-<Version>/install.sh    # gefuehrte Einrichtung
```

Die gefuehrte Einrichtung fragt nacheinander:
1. den Projektordner
2. das Curator-Modell (codex, claude oder command)
3. Hintergrunddienste und Widgets
4. omarchy-backup mit Ziel-Ordner
5. das Maschinengedaechtnis mit optionalem Spiegel-Ordner

Danach zeigt sie eine Zusammenfassung und richtet erst nach Bestaetigung ein.
Jeder Lauf schreibt ein Protokoll nach `~/.local/state/polaris/install-<Zeit>.log`. Das
Maschinengedaechtnis entsteht als Erstes, samt Links fuer Claude Code, Codex usw. Dazu
kommt `handoff/polaris.md` mit Stand und offenen Punkten. Bricht die Installation ab,
nennt sie Schritt, Grund, Protokoll und den Leitfaden
[`docs/AGENTEN-LEITFADEN.md`](docs/AGENTEN-LEITFADEN.md). Mit diesen Angaben kann ein
lokaler AI-Agent nach den Prinzipien des Systems selbststaendig weitermachen.
Den Leitfaden bei jeder Aenderung an Polaris mitpflegen.

Ohne Terminal oder mit Optionen (`--yes`, `--projects`, `--backup-target`,
`--memory-mirror` usw., siehe `install.sh --help`) laeuft sie ohne Fragen.

Aus einem Paket gestartet, kopiert `install.sh` die Programmdateien nach
`~/.local/share/polaris/app` und installiert von dort (Update: neues Paket
genauso ausfuehren, die alte Fassung landet unter
`~/.local/share/polaris/install-backups/`). Ist omarchy-backup eingerichtet,
traegt `install.sh` den Programmordner in dessen `paths.d/` ein (`repo` fuer
einen git-Checkout, `include` fuer den App-Ordner), sofern er dort noch nicht
erfasst ist. Aus dem Paket richtet es ausserdem omarchy-backup ein: Link,
Standardkonfiguration und Widget. Mit Ziel-Ordner schaltet es auch die
taeglichen Snapshots ein, die Baseline setzt man selbst.

Zum Maschinengedaechtnis:
- `install.sh` legt `~/.config/ai-agents` aus der Vorlage an, oder holt es aus dem Spiegel zurueck, wenn dort schon eines liegt.
- Es traegt das Gedaechtnis ins Backup ein und richtet `polaris-memory-mirror` mit Timer ein (alle 30 min).
- Ein vorhandenes Gedaechtnis bleibt unveraendert, nur `memlint.py` wird aktualisiert.
- `--no-backup` bzw. `--no-memory` schalten das ab.

**Spiegel des Gedaechtnisses** (`memory_mirror:` in `~/.config/polaris/config.yaml`):
- Gearbeitet wird immer lokal. Der Spiegel ist eine einseitige Kopie, z. B. nach `~/Projekte/Polaris/Maschinengedaechtnis`.
- Geloeschte oder geaenderte Dateien bleiben dort unter `.alt/<Datum>/`.
- Ist das Laufwerk nicht eingehaengt, endet der Lauf mit 75 und legt nichts unter dem Mountpunkt an. Der naechste Lauf holt nach.
- Der Health Check wird gelb, wenn der letzte erfolgreiche Lauf aelter als 2 Tage ist.

Die Bewertung einer austauschbaren Agent-Runtime steht in
[`docs/agent-runtime-evaluation.md`](docs/agent-runtime-evaluation.md), die
Entscheidung in [`docs/adr/001-agent-runtime.md`](docs/adr/001-agent-runtime.md).
Der dazugehörige isolierte, nicht produktive PoC liegt unter
[`experiments/agent-runtime-poc/`](experiments/agent-runtime-poc/README.md).

## Architektur

```
ChatGPT (chatgpt.com) / Claude (claude.ai), jeweils in Firefox
   |  ChatGPT: rohes Chat-Abbild aus der Web-Schnittstelle (chatgpt_api.js),
   |           Seitenaufbau (extractor.js) nur noch als Rueckfall
   |  Claude:  JSON-Schnittstelle der Web-App (claude.js)
   v
extension/  (Firefox WebExtension, MV3)
   |
   |  Hauptweg (seit v0.1.2): Native Messaging -> bridge/bin/polaris-native-host
   |     Firefox startet den Host nur bei Bedarf (~50 ms, ~14 MB), der Port
   |     schliesst nach 30 s Leerlauf. Kein Download, kein Downloads-Ordner.
   |  Rueckfall (Host nicht registriert/erreichbar): downloads.download()
   |     -> blob: URL (Firefox lehnt data: URLs dafuer ab)
   v
~/Downloads/polaris-inbox/*.json      (Bridge-Inbox; der Host schreibt atomar
   |                                   hierher und verarbeitet sofort)
   |  Rueckfall-Downloads: systemd --user path unit (polaris-bridge.path),
   |  Sicherheitsnetz alle 10 min: polaris-bridge.timer
   v
polaris-bridge.service  ->  `polaris process`
   |
   v
bridge/polaris/  (Python, stdlib + PyYAML)
   - schema.py    Validierung
   - snapshot.py  Chat-Abbild / Datenexport -> Turns (einzige Interpretation)
   - mapping.py   Projekt-ID (ChatGPT/Claude) -> lokaler Projektordner (persistiert)
   - matching.py  exakt / normalisiert / fuzzy
   - storage.py   atomare, append-only Ablage, Konfliktpruefung
   v
<lokales Projekt>/raw/<chatgpt|claude>/messages/<message-id>.json
<lokales Projekt>/raw/<chatgpt|claude>/conversations/<conversation-id>.json
```

Komponenten sind bewusst getrennt: Die Extension kennt keine Projektpfade und
interpretiert keine Chats, die Bridge kennt kein ChatGPT-DOM.

## Installationspfade

| Was | Pfad |
|---|---|
| Repo (Code) | `~/Projects/polaris-chatgpt-connector/` |
| Browser Extension | `~/Projects/polaris-chatgpt-connector/extension/` |
| Bridge (Python) | `~/Projects/polaris-chatgpt-connector/bridge/` |
| CLI-Wrapper | `~/.local/bin/polaris` (Symlink auf `bridge/bin/polaris`) |
| Config (project_roots) | `~/.config/polaris/config.yaml` |
| Projekt-Mappings | `~/.config/polaris/projects.yaml` |
| Browser-Inbox | `~/Downloads/polaris-inbox/` |
| Pending/Processed/Failed | `~/.local/share/polaris/{pending,processed,failed}/` |
| Log | `~/.local/state/polaris/polaris.log` |
| systemd Units | `~/.config/systemd/user/polaris-bridge.{service,path,timer}` (Kopien aus `systemd/`; Curator-Units siehe `curator/README.md`) |
| Erkannter Projekt-Root | `~/Projekte` (rclone-Mount von Google Drive "Projekte", siehe `~/.config/systemd/user/rclone-projekte.service`) |

`project_roots` muss kein Drive-Mount sein - jeder lokale oder anders
eingehaengte Ordner geht. Verwalten ohne Handarbeit in der YAML-Datei:

```bash
polaris roots                     # Eintraege mit Anzahl Projektordner
polaris roots add <Ordner>        # Ordner, in dem die Projektordner liegen
polaris roots remove <Ordner>     # verweigert, solange Zuordnungen hineinzeigen (--force)
```

Beim ersten Start wird nur `~/Projekte` automatisch eingetragen, sofern es
existiert. Das Extension-Popup bietet die Unterordner aller Eintraege zur
Zuordnung an; die Extension selbst braucht dafuer keine Aenderung. Der Health
Check (Zeile „Projektordner“) prueft jeden Eintrag: fehlt er oder fehlen
zugeordnete Projektordner darunter (typisch: Mount abgefallen), ist das ein
Fehler; nur bei rclone-Mounts zusaetzlich der Upload-Stau. Die Abhaengigkeit
von `rclone-projekte.service` in den Units ist `Wants=`/`After=` und schadet
ohne diese Unit nicht.

`~/Projekte` ist ausschliesslich fuer die eigentlichen Projektordner
(Rohdaten) gedacht. Der Code selbst liegt bewusst getrennt davon unter
`~/Projects` (lokal, nicht ueber Google Drive synchronisiert), damit kein
Git-Repo / keine Bridge-Zustandsdateien versehentlich in den Drive-Sync
geraten.

Es existiert bereits ein **anderes** Projekt namens "Polaris" unter
`~/Projekte/Polaris` (leere Test-Verzeichnisse eines Knowledge-Extraction-
Experiments). Das ist nicht dasselbe System wie dieser Connector und wurde
nicht angefasst.

## Extension installieren (Firefox)

Die Extension ist bei addons.mozilla.org (AMO) als **unlisted** Add-on
registriert (Add-on-ID 3070992, Extension-ID
`polaris-chatgpt-connector@mstarchy.local`) und wird dort signiert. Eine
signierte `.xpi` installiert sich dauerhaft, auch auf Release-Firefox.

### Oeffentlicher Spiegel

Dieses Repo bleibt privat (volle Historie). Freigegebene Staende gehen per
`scripts/publish-public.sh [<ref>] [--push]` als je ein Commit in den
oeffentlichen Spiegel (Arbeitskopie `~/Projects/polaris-public`), ohne
Historie und ohne Binaerpakete; vorher laeuft dieselbe Privatsphaere-Pruefung.
Tests und Kommentare verwenden deshalb nur erfundene Projektnamen (Kunde-A,
Musterfirma, ABC, Falke). Pull Requests aus dem Spiegel lassen sich per
`git format-patch | git am -3` uebernehmen (siehe Kopf des Skripts).
Anleitung fuer Beitragende und deren KI-Agenten: `CONTRIBUTING.md`.

### Neue Version ausliefern

1. Version in `extension/manifest.json` erhoehen.
2. ZIP aus dem Inhalt von `extension/` bauen (`manifest.json` muss im
   ZIP-Wurzelverzeichnis liegen), z. B.
   `(cd extension && zip -r ../polaris-chatgpt-connector-v<X.Y.Z>.zip .)`.
3. Bei AMO unter https://addons.mozilla.org/developers/addon/3070992/versions
   als neue **unlisted** Version hochladen; AMO signiert automatisch.
4. Signierte `.xpi` herunterladen und in Firefox installieren (Drag&Drop oder
   `about:addons` -> Zahnrad -> „Add-on aus Datei installieren“).
5. Die signierte `.xpi` als `dist/polaris-chatgpt-connector-<X.Y.Z>-signed.xpi`
   committen (alte entfernen), damit sie nach einer Neuinstallation ohne
   AMO-Login bereitliegt; `install.sh` zeigt auf die neueste.
6. `scripts/publish-public.sh --push`: prueft die entpackte `.xpi` (Version,
   ID, Signatur, Privatsphaere), legt im oeffentlichen Spiegel das Release
   `v<X.Y.Z>` mit der `.xpi` an und aktualisiert dort `updates.json`.

Seit 0.4.2 steht im Manifest eine `update_url`
(`raw.githubusercontent.com/mstio/polaris-connector/main/updates.json`):
installierte Kopien, auch bei Dritten, fragen etwa einmal am Tag nach und
aktualisieren sich selbst. 0.4.1 und aelter kennen sie noch nicht und
muessen einmal von Hand auf 0.4.2 gebracht werden. Die `update_url` nie
aendern: alte Installationen fragen sonst ins Leere. Erteilte
Berechtigungen bleiben an der Extension-ID haengen und ueberstehen Updates.

### Berechtigung fuer chatgpt.com / claude.ai

Firefox behandelt `host_permissions` von MV3-Extensions als optional. Ohne
Erteilung laeuft die Extension nur nach Klick aufs Symbol. Im Popup
„Zugriff dauerhaft erlauben“ klicken oder in `about:addons` -> Polaris ->
Berechtigungen beide Seiten freigeben.

### Entwicklung: temporaer laden

Zum Testen eines ungesignierten Stands: `about:debugging#/runtime/this-firefox`
-> „Temporäres Add-on laden…“ -> `extension/manifest.json`. Der temporaere
Stand ueberlagert die installierte Version bis zum naechsten Firefox-Neustart;
danach laeuft wieder die signierte Version (der Health Check zeigt die
Ueberlagerung gelb an).

### Native Messaging einrichten (seit v0.1.2)

```bash
bridge/install-native-host.sh           # registriert polaris_bridge bei Firefox
bridge/install-native-host.sh --remove  # Registrierung entfernen
```

Schreibt `~/.mozilla/native-messaging-hosts/polaris_bridge.json` (verweist
auf `bridge/bin/polaris-native-host` in diesem Repo). Firefox sucht dort auch,
wenn das Profil unter `~/.config/mozilla/` liegt. Das Skript legt `~/.mozilla`
bewusst nicht selbst an. Ohne Registrierung funktioniert die Extension
unveraendert ueber Downloads. Protokoll und Verhalten: `bridge/polaris/native_host.py`.

Die Extension merkt sich pro Unterhaltung, welche Nachrichten bereits
uebergeben wurden (`storage.local`, nur IDs, max. 500 Unterhaltungen);
eine Rueckkehr in eine Unterhaltung loest daher keinen erneuten Export aus.

### Luecken aus dem offiziellen ChatGPT-Export schliessen

Die Extension archiviert nur Unterhaltungen, die im Browser geoeffnet werden.
Fehlendes (z. B. nach einem Ausfall) laesst sich aus dem offiziellen Export
nachholen: ChatGPT -> Einstellungen -> Datenkontrollen -> Daten exportieren,
ZIP aus der Mail herunterladen, dann:

```bash
polaris import-export ~/Downloads/<export>.zip --since 2026-09-14 --dry-run   # anzeigen
polaris import-export ~/Downloads/<export>.zip --since 2026-09-14             # importieren
```

Nur Projekt-Unterhaltungen (`gizmo_id` = `g-p-...`), nur der aktuelle
Gespraechsstrang, bereits vorhandene Nachrichten werden uebersprungen.
Projekte ohne Mapping werden gemeldet (`polaris map <id> <Ordner>`).
Der Curator verarbeitet den Nachschub danach wie gewohnt tageweise.

### Aeltere Chats lesbar ablegen, ohne Kuration (seit 2026-10-02)

Fuer grosse Mengen alter Chats ist Kuration zu teuer und verzerrt die
Gewichtung. `archive-export` legt sie stattdessen als Markdown ab - nichts
landet in `raw/`, der Curator wertet sie nicht aus:

```bash
polaris archive-export ~/Downloads/<entpackter-export>/ --since 2025-10-02 --min-turns 6 \
    --project Kunde-A --project Kunde-B --dry-run
```

Ergebnis: `<Projekt>/ChatGPT-Archiv/<JJJJ-MM-TT> <Titel>.md` (Frontmatter mit
Chat-ID, Zeitraum, `kuratiert: nein`; Verlauf als „Ich“/„ChatGPT“) und
`ChatGPT-Archiv/Dateien/<Chat>/<Originalname>`. Abzweigungen („Teilgespraech“)
verweisen fuer den uebernommenen Verlauf auf den Ausgangs-Chat. `.pst/.mbox/.zip`
werden nur vermerkt; hochgeladene Dateien, die der Export nicht enthaelt,
werden auf eine gleichnamige Datei im Projektordner verlinkt, sonst vermerkt.
Wiederholbar (bereits abgelegte Chat-IDs werden uebersprungen). Einzelne
Chats lassen sich spaeter gezielt kuratieren, indem man sie in `inbox/` legt.

### Claude (claude.ai, seit v0.2.0)

Dieselbe Extension exportiert auch Claude-Projekt-Unterhaltungen
(`extension/claude.js`). Gelesen wird nicht der Seitenaufbau, sondern die
JSON-Schnittstelle, die die Claude-Web-App selbst laedt
(`/api/organizations/<org>/chat_conversations/<id>?tree=True...`): nur der
aktuelle Gespraechsstrang, nur Textinhalt (keine Denkprozesse, keine
Werkzeugaufrufe), Dateinamen aus `attachments`/`files`. Projekt-IDs heissen
`claude-p-<uuid>`, Ablage unter `<Projekt>/raw/claude/`. Claude-Projektnamen
passen selten zum Ordnernamen - neue Claude-Projekte landen daher zunaechst
in der Warteschlange (`polaris pending`). Zugeordnet wird im Popup unter
„Ablageordner“ (Auswahl nur aus Ordnern unter den `project_roots`, geliefert
vom Native Host; wartende Nachrichten werden sofort nachverarbeitet) oder per
`polaris map claude-p-<uuid> <Ordner>`. `suggested_folder_for_claude` in
`~/.config/polaris/config.yaml` legt den Vorschlag fuer neue Claude-Projekte
fest (wird nie automatisch angewendet). Dieselbe Auswahl aendert auch
ChatGPT-Zuordnungen. Sync pro Projekt wie bei ChatGPT im Popup einschalten; die
Berechtigung fuer claude.ai muss in Firefox erteilt sein (Popup-Hinweis).

### ChatGPT als Chat-Abbild ueber die Web-Schnittstelle (seit v0.4.0)

Die Extension liest ChatGPT-Unterhaltungen standardmaessig ueber die
Schnittstelle der Web-App (`extension/chatgpt_api.js`: `/api/auth/session`
fuer das Sitzungstoken, dann `/backend-api/conversation/<id>`) und reicht das
**rohe** JSON als Chat-Abbild an die Bridge weiter (Native Host
`export-snapshot`, sonst Abbild-Datei im Inbox-Ordner). Sie interpretiert
nichts. Nur die Bridge bildet daraus Turns (`bridge/polaris/snapshot.py`) -
mit derselben Funktion wie fuer den offiziellen Datenexport, der dasselbe
Format hat:

- Projekt aus den Anbieterdaten (`gizmo_id`); der Hinweis der Seite
  (Projekt/Chat aus der Adresse) wird nur gegengeprueft - weicht er ab, wird
  nichts uebernommen. Chats ohne Projekt werden nie archiviert.
- Turn = Nutzer-Nachricht bzw. alles zwischen zwei Nutzer-Nachrichten
  (Assistent, Werkzeugschritte, Denkprozesse); ID = frueheste Nachricht,
  `member_ids` = alle. Ist ein Bestandteil im Chat schon archiviert (z. B.
  frueher per DOM unter einer anderen ID), gilt dessen ID - keine Dubletten.
- Eine laufende letzte Antwort (Werkzeugschritt, `in_progress`,
  `end_turn=false`) wartet auf das naechste Abbild. Die Extension fragt alle
  20 s nach (nur sichtbarer Tab) und sendet nur bei geaenderter `update_time`.

Warum (Vorfall 2026-09-29): bis 0.3.0 entschieden DOM-Erfassung,
Schnittstellen-Modus und Export-Import getrennt, was ein Turn ist, welche ID er
hat und wann er fertig ist - Quelle von Vermischungen, Dubletten und
abgeschnittenen Antworten. Der Seitenaufbau (DOM) wird nur noch gelesen, wenn
die Schnittstelle nicht antwortet (Popup zeigt den Grund); dieser Rueckfall ist
gegen veralteten Seiteninhalt abgesichert (kein Export ohne Chat-ID, Abbild
beim Chatwechsel, Text-Schluessel fuer Turns ohne ID, letzte Antwort erst nach
8 s ohne Aenderung) und schickt ebenfalls `member_ids`. Abschaltbar im Popup
(„ChatGPT über Schnittstelle lesen“).

### Speicherwaechter fuer ChatGPT- und Claude-Tabs (seit v0.4.4)

Anlass: Am 22. und 23.10.2026 wuchs ein ChatGPT-Tab auf 20-26 GB (RAM + Swap);
systemd-oomd beendete daraufhin ganz Firefox mit allen Tabs. Firefox gibt
Erweiterungen keinen Zugriff auf den Speicher eines Tabs, deshalb misst der
Native Host (`bridge/polaris/tab_memory.py`): Er ist Kind des
Firefox-Hauptprozesses und liest dessen Tab-Prozesse (unter dem Forkserver)
aus `/proc`, gezaehlt wird VmRSS + VmSwap des groessten.

- `background.js` fragt jede Minute (`alarms`), aber nur solange ein
  ChatGPT- oder Claude-Tab offen ist (`tab-memory` an den Host).
- Ab **4 GiB** Desktop-Meldung (normal), ab **8 GiB** dringend; erneut nur bei
  hoeherer Stufe, anderem Prozess oder alle 10 min, solange kritisch
  (Zustand: `~/.local/state/polaris/tab-memory.json`).
- `memory_banner.js` zeigt in ChatGPT- und Claude-Tabs eine Hinweisleiste mit
  Groesse und „F5 druecken (Neu laden)“; ausgeblendet bleibt sie aus, bis die
  Stufe steigt.
- Welcher Prozess zu welcher Seite gehoert, verraet `/proc` nicht; die
  Meldung sagt deshalb „vermutlich“. Kein automatisches Neuladen (Entwuerfe
  und laufende Antworten gingen verloren).
- Kosten: ein Host-Start (~60 ms CPU) pro Minute, nur bei offenem Tab.

## Polaris Sync aktivieren

1. Einen ChatGPT-Chat oeffnen, der zu einem ChatGPT-Project gehoert.
2. Extension-Icon in der Toolbar klicken.
3. Erkannter Projektname erscheint. "Sync project" auf ON stellen. Bei
   neuen Claude-Projekten (und nicht eindeutig zuordenbaren ChatGPT-Projekten)
   unter „Ablageordner“ den Zielordner waehlen und „Zuordnen“ klicken.
4. Ab jetzt werden neue, abgeschlossene Turns in diesem Projekt automatisch
   exportiert. Einzelne Chats koennen ueber "Exclude this chat" ausgenommen
   werden.
5. "Sync now" exportiert manuell (Fallback, z.B. direkt nach dem
   Aktivieren fuer bereits vorhandene Nachrichten im aktuellen Chat).

Neue Projects sind per Default `Sync project = OFF`.

## Bridge manuell starten

```bash
polaris process
```

Verarbeitet einmalig alle Dateien in `~/Downloads/polaris-inbox/` sowie
alle bislang unaufgeloesten Pending-Events.

## systemd-Automatik

Einrichten (Units liegen im Repo unter `systemd/`):

```bash
cp systemd/polaris-bridge.{service,path,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now polaris-bridge.path polaris-bridge.timer
```

Neue Datei in der Inbox -> `polaris-bridge.path` -> startet
`polaris-bridge.service` (oneshot) -> `polaris process` -> beendet sich.
Kein dauerhaft laufender Poll-Prozess. `polaris-bridge.timer` startet
denselben Dienst zusaetzlich 5 min nach dem Boot und dann alle 10 min als
Sicherheitsnetz fuer liegengebliebene Dateien (seit 2026-09-29). Der Dienst
hat `StartLimitIntervalSec=0`, damit viele Inbox-Dateien ihn nicht
stilllegen.

## `polaris status`

```bash
polaris status
```

Zeigt Inbox-Groesse, Anzahl verarbeiteter Events, bekannte/pending
Projekte, bekannte Conversations/Messages und den Zeitpunkt des letzten
Laufs.

## Projekt-Mapping

Automatisch, in dieser Reihenfolge: bereits bekanntes Mapping ->
exakter Name -> normalisierter Name (Gross/Klein, `_`/`-`,
Nummerierungspraefixe) -> Fuzzy Match nur bei eindeutig hoher Sicherheit.
Bei Unsicherheit: Pending, niemals raten.

```bash
polaris projects              # bekannte Mappings anzeigen
polaris map <project-id> <lokaler-pfad>
polaris unmap <project-id>
```

`polaris map` verarbeitet automatisch alle bereits wartenden Pending-
Events dieses Projekts nach.

Der Zielpfad muss unterhalb eines konfigurierten `project_roots`-Eintrags
liegen (`~/.config/polaris/config.yaml`); alles andere wird abgelehnt
(Schutz vor Fehlzuordnung/Pfad-Traversal).

## Pending-Projekte

```bash
polaris pending
```

Zeigt unbekannte ChatGPT- und Claude-Projekte inkl. moeglicher Kandidaten (nur
informativ, keine automatische Auswahl). Danach `polaris map` verwenden.

## Logs

```bash
tail -f ~/.local/state/polaris/polaris.log
```

Enthaelt ausschliesslich technische Metadaten (IDs, Rollen, Mapping,
Zielpfad, Status) - niemals Chat-Inhalte.

## Fehlerdiagnose

- Datei bleibt in `~/Downloads/polaris-inbox/`: Extension-Download ist
  fehlgeschlagen oder Bridge lief noch nicht (`polaris process` manuell
  ausfuehren, `systemctl --user status polaris-bridge.path` pruefen).
- Datei landet in `~/.local/share/polaris/failed/`: ungueltiges JSON oder
  Schema. Grund steht im Log.
- Datei landet in `~/.local/share/polaris/pending/<project-id>/`: Projekt
  konnte nicht sicher zugeordnet werden. `polaris pending` +
  `polaris map` verwenden.
- Grund `dom-after-no-project`: ChatGPT meldete fuer diesen Chat zuletzt
  ausdruecklich kein Projekt; eine Ersatzerfassung aus der Seite (Projekt nur
  aus der evtl. veralteten Adresse) wird 7 Tage lang abgelehnt. Chat neu
  laden, sobald er wieder in einem Projekt liegt (seit 2026-09-29).
- Konflikte (`polaris conflicts`): einzeln mit `--resolve <Datei> --keep
  existing|rejected`. Wurde ein Chat in ChatGPT in ein anderes Projekt
  verschoben (derselbe Chat, anderes Projekt), alle auf einmal mit
  `polaris conflicts --resolve-chat <Chat-ID> --keep rejected`. Konflikte
  zwischen verschiedenen Chats (Vermischung) klaert `--resolve-chat` bewusst
  nicht.
- Letzte Antwort fehlt: Die Bridge haelt eine noch laufende letzte Antwort
  zurueck, bis ein fertiges Abbild kommt. `polaris held-back` zeigt die
  betroffenen Chats (`--clear <Schluessel>|all` bereinigt, Schluessel wie in der Ausgabe); Chat oeffnen oder
  „Sync now“ hilft.
- Extension exportiert nichts: DevTools-Konsole auf der ChatGPT- bzw.
  Claude-Seite pruefen (`[Polaris]`-Fehler), Popup pruefen, ob ein Projekt
  erkannt wurde und ob die Schnittstelle nutzbar ist, Berechtigung pruefen
  (siehe „Berechtigung fuer chatgpt.com / claude.ai“).

## Extension nach ChatGPT-/Claude-Aenderungen reparieren

Seit v0.4.0 liest die Extension ChatGPT ueber die Web-Schnittstelle
(`extension/chatgpt_api.js`), Claude ebenfalls ueber dessen Schnittstelle
(`extension/claude.js`). Beide Schnittstellen sind nicht dokumentiert; aendert
sich ihr Format, zeigt das Popup „Schnittstelle gerade nicht nutzbar …“ und
der Health Check meldet ausbleibende Archivierung. Dann zuerst dort ansetzen;
Luecken lassen sich ueber den Datenexport schliessen.

Der Seitenaufbau (DOM) wird nur noch als Rueckfall gelesen. Die gesamte
DOM-spezifische Logik liegt ausschliesslich in `extension/extractor.js`.
Wenn ChatGPT sein UI aendert:

1. Auf chatgpt.com DevTools oeffnen, ein Turn-Element inspizieren.
2. Nachrichten: Seit 2026-09-25 liest `getUnitTurns()` Elemente mit
   `data-chatgpt-search-unit-key` (Rolle am Ende, z. B. `...:assistant`),
   `data-chatgpt-search-message-ids` (ID) und `data-markdown-text-style`
   (Text); das fruehere Markup (`conversation-turn-*`, `data-message-id`,
   `data-message-author-role`) wird zuerst versucht. Ohne DOM-Wissen
   pruefbar mit einer Struktur-Probe in der Konsole, die nur Attribute
   ausgibt (siehe Kommentar ueber `UNIT_SELECTOR` in `extractor.js`).
3. Streaming-Erkennung: pruefen, ob `STREAMING_SELECTOR` noch einen
   "Stop generating"-Button trifft, waehrend eine Antwort generiert wird.
   Das ist die einzige Annahme im System, die nie gegen eine echte,
   laufende Generierung verifiziert wurde - am wahrscheinlichsten
   reparaturbeduerftig, falls der Auto-Export ausbleibt.
4. Projekt-Erkennung: kommt seit 2026-09-25 aus der Adresse
   (`/g/g-p-<id>[-slug]/c/...` bzw. `/project`, `PATH_PROJECT_RE`); das DOM
   liefert nur noch den Anzeigenamen. Der frueher genutzte
   `aria-label="Open <Name> project"`-Link existiert nicht mehr.
5. Extension in `about:debugging` temporaer laden, auf einer
   Testkonversation pruefen (Konsole beobachten), danach als neue Version
   ausliefern (siehe „Neue Version ausliefern“).

Die uebrigen Dateien (`content.js`, `background.js`, `popup.*`) sollten von
reinen Markup-Aenderungen unberuehrt bleiben.

## Vollstaendige Deinstallation

```bash
systemctl --user disable --now polaris-bridge.path polaris-bridge.timer
rm ~/.config/systemd/user/polaris-bridge.{path,service,timer}
systemctl --user daemon-reload

bridge/install-native-host.sh --remove   # vor dem Loeschen des Repos

rm ~/.local/bin/polaris
rm -rf ~/Projects/polaris-chatgpt-connector

rm -rf ~/.config/polaris
rm -rf ~/.local/share/polaris
rm -rf ~/.local/state/polaris
rm -rf ~/Downloads/polaris-inbox
```

Curator-Units und -Dateien entfernt man getrennt (siehe `curator/README.md`).
Bereits archivierte Rohdaten unter `<Projekt>/raw/{chatgpt,claude}/` in den
jeweiligen Projektordnern bleiben davon unberuehrt (bewusst - das sind
deine Projektdaten, keine Bridge-internen Dateien) und muessen bei
Bedarf manuell entfernt werden.

Extension in Firefox: `about:addons` -> Polaris -> „Entfernen“.

## Tests

```bash
cd tests
python3 -m unittest discover          # Bridge + (falls node vorhanden) Extension
node --test tests/extension/          # nur Extension (aus dem Repo-Wurzelordner)
cd curator/tests && POLARIS_NO_NOTIFY=1 python3 -m unittest discover
```

Extension-Tests (seit 2026-09-29, ohne Abhaengigkeiten): `harness.mjs`
(vm-Sandbox mit Fake-Uhr/Timern/DOM/fetch fuer `content.js`), `minidom.mjs`
(kleines DOM fuer `extractor.js`), eigene Umgebungen fuer `claude.js` und
`background.js`. `POLARIS_CONTENT_JS=<datei>` prueft einen anderen Stand von
`content.js` - so laesst sich zeigen, dass ein Test den alten Fehler erkennt.

Bridge: 143 Tests (Stand 2026-10-23), reines `unittest` (kein pytest auf
diesem System installiert); einer davon startet die 56 Node-Tests der
Extension, sofern `node` vorhanden ist. Abgedeckt u. a.: Matching, Schema,
atomare append-only Ablage, Revisionen, Pending -> Mapping ->
Nachverarbeitung, Chat-Abbild/Turns (`test_snapshot.py`), Konflikte und
Quarantaene (`test_conflicts.py`), Native Host, Datenexport-Import und
-Archiv. Tests laufen nur gegen temporaere Verzeichnisse; zur Sicherheit mit
`POLARIS_NO_NOTIFY=1` und bei Bedarf umgeleitetem `HOME` starten.

## Bekannte Annahmen / Risiken

- ChatGPT- und Claude-Schnittstellen (`/backend-api/conversation/<id>`,
  `/api/organizations/<org>/chat_conversations/<id>`) sind nicht
  dokumentiert und koennen sich ohne Ankuendigung aendern. Symptom: Popup
  meldet die Schnittstelle als nicht nutzbar, Health Check „keine
  ChatGPT-Nachricht seit N Tagen“.
- Der DOM-Rueckfall (`extractor.js`) haengt am aktuellen ChatGPT-Markup
  (zuletzt am 2026-09-25 live neu bestimmt). Dort liefert `innerText` nur den
  gerenderten Text ohne Markdown-Formatierung, Parent-Beziehungen werden aus
  der DOM-Reihenfolge abgeleitet (`parent_id_source: "dom-order"`), und
  `STREAMING_SELECTOR` (Erkennung laufender Antworten ueber sichtbare
  Stop-Knoepfe) ist die am wenigsten belastbare Annahme.
- Claude-Nachrichten haben keine Revisionen: eine beim Erfassen noch
  unfertige Claude-Antwort bleibt unvollstaendig (Abhilfe braeuchte eine neue
  Extension-Version).
- Die Extension archiviert nur Chats, die im Browser geoeffnet werden;
  ChatGPT-Luecken schliesst der offizielle Datenexport
  (`polaris import-export`).
