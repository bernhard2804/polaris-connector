# Polaris Curator v0.2

Kondensiert neue ChatGPT-Messages (aus der Polaris Bridge) sowie neue
Projektdokumente (`inbox/mail`, `inbox/documents`, `inbox/notes`) zu
kuratiertem, belastbarem Projektwissen: `knowledge.md`, `decisions.md`,
`open-loops.md`. Keine RAG, keine Embeddings, keine Vector-DB - reine
Extraktion, Redaktion und gezieltes Merging. Laeuft projektuebergreifend
(Firma, Kunden, private Vorhaben) - die
Klassifikations-/Qualitaetslogik unten ist bewusst projektneutral.

**v0.2-Aenderung (2026-09-14)**: Nach einem Qualitaets-Review eines
echten Testlaufs (siehe "Qualitaets-Review v0.1 -> v0.2" unten) wurde die
Extraktion von einem flachen "is_assumption"-Flag auf eine erzwungene
5-Kategorien-Klassifikation pro Eintrag umgestellt (Fakt /
Stakeholder-Aussage / Hypothese / Systemische Implikation /
Entscheidung), inkl. Scope, Evidenzgrad, Konfidenz, Wirkungsketten,
Key-Person-Erkennung und einer verpflichtenden Selbstkritik-Checkliste
vor jeder Antwort. Das Modell (`gpt-5.6-luna`) und die
Single-Call-Architektur wurden dabei bewusst NICHT geaendert - siehe
"Modellstrategie" unten, warum.

## Architektur

```
raw/chatgpt/messages/*.json  ---\
                                  +--> Tagesbatches (Projekt + lokaler Tag)
inbox/{mail,documents,notes} ---/       |
                                         v
                                  Budget-Pruefung (max_batches_per_run, ...)
                                         |
                                         v
                                  Prompt (Batch/Dokument + aktuell
                                  kuratiertes Wissen als Kontext)
                                         |
                                         v
                          codex exec --output-schema (strukturiertes JSON)
                                         |
                                         v
                          lokale Schema-Validierung (change_schema.py)
                                         |
                                         v
                    Selbstkritik-Gate (change_schema.failed_self_review_checks)
                                         |
                                         v
                    Merge-Engine (merge.py) -> knowledge.md / decisions.md /
                                                open-loops.md (atomar)
                                         |
                                         v
                          curator-state.json NUR nach erfolgreichem Merge
```

Extraktion und Kuratierung laufen bewusst weiterhin in EINEM Schritt/
einem `codex exec`-Aufruf (kein separater Extraktions- vs.
Synthese-Call) - siehe "Modellstrategie" unten, warum das (noch) so ist.
Was sich in v0.2 geaendert hat: Das Modell muss dabei eine strukturierte
Zwischenrepraesentation pro Eintrag liefern (Klassifikation, Scope,
Evidenzgrad, Konfidenz, ...), aus der `merge.py` erst die finale
Markdown-Formulierung baut - die analytische Bewertung ist damit von der
Formulierung getrennt (siehe "Qualitaetslogik" unten), auch ohne eigenen
LLM-Call dafuer.

Module (`curator/polaris_curator/`):
- `config.py` - globale + projektspezifische Konfiguration, Modell-IDs
- `chatgpt_source.py` - liest raw/chatgpt/messages/, liefert nur neue
- `documents_source.py` - inbox-Scan, Hash-basierte Aenderungserkennung,
  Extraktion (.eml inkl. Anhaenge/.pdf/.docx/.xlsx/.pptx/.csv/.md/.txt),
  Ablage der Mail-Anhaenge
- `office_extract.py` - DOCX/XLSX/PPTX-Text nur mit der Standardbibliothek
- `batching.py` - Tagesbatches, Sub-Batches bei Budget-Ueberschreitung
- `prompts.py` - Prompt-Bau inkl. Prompt-Injection-Schutz
- `llm.py` - `codex exec`-Aufruf, Fallback-Modell, Fehlerklassifizierung
- `change_schema.py` - JSON-Schema (fuer `--output-schema`) + Validator
- `merge.py` - gezieltes Merging in die drei Markdown-Dateien
- `state.py` - `curator-state.json`
- `pipeline.py` - Orchestrierung, Budget, Locking
- `reclassify.py` - einmalige Neueinstufung nach Regel 2a
- `integrity.py` - Projekttrennung der Rohdaten pruefen (Sperre vor jedem Lauf, Health Check)
- `retract.py` - `retract`: kuratierte Eintraege aus bestimmten Nachrichten zuruecknehmen
- `transfer.py` - `export`: kuratiertes Wissen als ZIP fuer andere Anbieter
- `cli.py` - `polaris-curator status/status-all/run/run-all/backfill/pending/export/...`

## Konfiguration

Global: `~/.config/polaris/curator.yaml` (wird beim ersten Lauf mit
Standardwerten angelegt). Projektspezifischer Override (optional):
`<Project>/.polaris/curator-config.yaml` (gleiches Format, nur die
gesetzten Felder ueberschreiben den globalen Wert).

```yaml
provider: openai
runtime: codex
model: gpt-5.6-luna        # kostenguenstiges Modell, siehe Hinweis unten
fallback_model: gpt-5.6-sol
permit_more_expensive_fallback: false
llm_timeout_seconds: 180
batching:
  min_messages_for_early_run: 10   # aktuell ohne Wirkung, siehe unten
  min_chars_for_early_run: 8000    # aktuell ohne Wirkung, siehe unten
budget:
  max_batches_per_run: 3
  max_llm_calls_per_run: 3
  max_messages_per_batch: 40
  max_input_chars_per_batch: 50000
  max_total_input_chars_per_run: 120000
context:
  max_curated_context_chars: 50000
  full_context_chars: 80000     # darueber: Verzeichnis + Volltext des Relevanten
  index_cap: 0.25               # Verzeichnis hoechstens 25 % davon (0 = alle Eintraege)
attachments:
  min_image_bytes: 300000       # kleinere Bilder = Signatur/Logo, ignoriert
  max_images_per_document: 5
  max_chars_per_attachment: 20000
```

**Modell-IDs**: `gpt-5.6-luna` wurde am 2026-09-12 aus
`~/.codex/models_cache.json` als "Fast and affordable agentic coding
model" ermittelt - passend fuer eine reine Extraktions-/Merge-Aufgabe.
`gpt-5.6-sol` war zu dem Zeitpunkt das globale Codex-Standardmodell des
Nutzers und dient hier als Fallback. Modellkataloge veralten - bei Bedarf
in `~/.config/polaris/curator.yaml` anpassen, ohne Code zu aendern.

**`min_messages_for_early_run` / `min_chars_for_early_run`**: In der
Aufgabenstellung fuer ein moegliches haeufigeres Aufruf-Intervall gedacht.
Da v0.1 nur ueber manuelle Aufrufe und den taeglichen systemd-Timer
laeuft - beides zaehlt laut Aufgabenstellung bereits als gueltiger Trigger
zum Verarbeiten auch kleiner Batches - haben diese Schwellenwerte aktuell
keine Wirkung; jeder Lauf verarbeitet alles, was ins Budget passt. Die
Konfiguration ist vorbereitet, falls spaeter ein haeufigerer Modus
ergaenzt wird.

**Runtime `claude` (seit 2026-10-07)**: Auf Rechnern ohne Codex kuratiert
die Claude-Code-CLI (`runtime: claude`, `model: opus`, `fallback_model:
sonnet`; Vorlage `curator.example-claude.yaml`). Gleicher abgeschotteter
Aufruf wie beim Ersatzmodell. Bilder aus Mail-Anhaengen entfallen dabei
(Warnung im Log), der Text wird kuratiert. `reviewer_model` dort aus lassen
- Claude prueft sonst sich selbst.

**Runtime `command` (seit 2026-10-07, anbieterneutral)**: beliebige
Agenten-CLI als Argumentliste (`command`), Platzhalter `{model}`,
`{prompt_file}`, `{schema_file}`, `{schema_json}`; ohne `{prompt_file}` kommt
der Prompt ueber stdin (im Dateimodus ist stdin geschlossen). Fehlt ein
Schema-Platzhalter, haengt der Curator das JSON-Schema an den Prompt an. Die
Antwort wird aus reinem JSON, einer JSON-Huelle (`command_output_key` oder
bekannte Felder wie `response`/`result`) oder dem JSON-Block in Text gelesen
und wie immer vom Curator selbst geprueft. Vorlage mit getestetem Beispiel
(Gemini/Antigravity im Planmodus) und ungetesteten Kandidaten:
`curator.example-command.yaml`. Sicherheit: Die CLI laeuft in einem leeren
Arbeitsordner; Shell- und Schreibwerkzeuge ueber ihre eigenen Flags
abschalten - die Chats sind fremder Inhalt. Fehlermeldungen enthalten nur
kurze Fehlerzeilen, nie den Prompt. Bilder aus Mail-Anhaengen entfallen.

**Einrichtung pruefen**: `polaris-curator llm-check` zeigt Runtime, Modell
und ob die benoetigten CLIs (Runtime, ggf. Claude als Ersatz/Pruefer) im
`PATH` liegen. `--call` macht zusaetzlich einen winzigen echten Aufruf des
Hauptmodells ohne Ersatzstufen (Anmeldung, Modellname; bei Codex ca. 20k
Token Vorlauf). Der Health Check meldet eine fehlende Runtime-CLI als
Fehler („Curator › Modell“).

## Erster Lauf / Initialisierung

Der erste Lauf pro Projekt verarbeitet den kompletten, zu diesem
Zeitpunkt vorhandenen ChatGPT-Bestand einmalig (siehe Aufgabenstellung
Abschnitt 4). Das ist normal und gewollt. Danach werden nur noch neue
Messages verarbeitet.

## CLI

```bash
polaris-curator status <Projekt>       # Ueberblick: neue Quellen, Budget
polaris-curator status-all             # Ueberblick ueber alle bekannten Projekte
polaris-curator run <Projekt>          # echten Lauf ausfuehren
polaris-curator run <Projekt> --dry-run
polaris-curator run-all                # alle bekannten Projekte (Bridge-Mapping)
polaris-curator backfill <Projekt>     # historischen Bestand ERNEUT verarbeiten
polaris-curator pending <Projekt>      # jenseits des Budgets wartende Einheiten
polaris-curator health [--json]        # Health-Check der eigenen Bausteine
polaris-curator compact <Projekt>      # knowledge.md verdichten (--dry-run zuerst)
polaris-curator index-documents <Projekt>|--all   # dokumente-index.md neu aufbauen
polaris-curator export <Projekt>|--all [-o ZIP]   # kuratiertes Wissen fuer anderen Anbieter
polaris-curator reclassify <Projekt> [--apply]    # KI-Antworten nach Regel 2a neu einstufen
polaris-curator retract <Projekt> <IDs>|--from-dir D [--apply [--rebuild]]  # Wissen aus Nachrichten zuruecknehmen
```

Fuer lokale Oberflaechen und Automatisierung liefern `status`, `status-all`,
`run` und `run-all` mit `--json` einen stabilen, maschinenlesbaren Vertrag.
Das Omarchy-Widget unter `omarchy-plugin/mst.polaris-curator/` verwendet
ausschliesslich diese JSON-Ausgaben und startet damit dieselbe Pipeline wie
der taegliche Timer: neue ChatGPT-Nachrichten und neue Dateien in
`inbox/{documents,mail,notes}` werden gemeinsam kuratiert.

`<Projekt>` ist entweder der Ordnername (z.B. `Firma`, muss unter
einem der Bridge-`project_roots` gemappt sein) oder ein direkter Pfad.

**`backfill` mit Vorsicht nutzen**: verarbeitet Messages unabhaengig vom
bisherigen State erneut - kann zu redundanten Eintraegen in `knowledge.md`
fuehren, wenn diese bereits kuratiert wurden. Nur bewusst einsetzen, z.B.
nach einem gewollten Reset der kuratierten Dateien.

## Health-Check und Überwachung (seit 2026-09-25)

`polaris-curator health [--json]` prüft die selbst gebauten Bausteine
(ChatGPT-Archivierung, Firefox-Erweiterung inkl. Berechtigung für
chatgpt.com, Native Host, Curator-Timer, Modell-CLI, omarchy-backup, Projektordner)
und klein ein paar Systemwerte (Platten, fehlgeschlagene Units, Akku). Das
Omarchy-Widget (`omarchy-plugin/mst.polaris-curator`, Anzeigename „Health
Check“) zeigt das als Ampel. Nur Zustandsdateien und Metadaten, keine teuren
Prüfungen. Optionale Bausteine (seit 2026-10-07): Ist omarchy-backup nicht
installiert bzw. `~/.config/ai-agents` nicht vorhanden, steht dort „nicht
eingerichtet (optional)“ statt eines Fehlers; installiertes omarchy-backup
ohne Snapshot ist eine Warnung.

Stille Ausfälle (`health.py`): kommt projektübergreifend länger als
`monitoring.max_days_without_chatgpt` (Default 3) keine ChatGPT-Nachricht an,
oder bleiben Dateien in der Bridge-Inbox liegen, warnt `status-all`/`run-all`;
der Timer-Lauf schickt höchstens eine Desktop-Benachrichtigung pro Tag und
Befund. Wer ChatGPT nicht (mehr) nutzt, schaltet die ChatGPT-Prüfung mit
`max_days_without_chatgpt: 0` aus. Wurde noch nie eine ChatGPT-Nachricht
archiviert, aber Claude-Nachrichten kommen an, bleibt sie auch ohne das still.

## Projektwissens-Transfer zwischen Anbietern (seit 2026-09-25)

```bash
polaris-curator export Firma          # ~/Downloads/polaris-projektwissen-Firma-<Zeit>.zip
polaris-curator export --all -o /pfad.zip  # alle bekannten Projekte in einem ZIP
polaris-curator export Firma --with-archive --json
```

Rueckweg des kuratierten Wissens: Was aus ChatGPT, Claude oder Dokumenten
kuratiert wurde, soll jedem Anbieter (und anderen Agenten) als Projektkontext
zur Verfuegung stehen, ohne dass das Wissen einem Anbieter gehoert. Kanonisch
bleiben die Markdown-Dateien im Projektordner; `export` packt davon eine
Momentaufnahme: `knowledge.md`, `decisions.md`, `open-loops.md`,
`dokumente-index.md` (mit `--with-archive` auch `knowledge-archiv.md`)
unveraendert inkl. `polaris-source`-Provenance, dazu ein erzeugtes
`LIESMICH.md` (Lesehilfe, Herkunftszaehlung pro Datei). **Nicht** enthalten:
`raw/` (Rohchats), `inbox/`, Originaldokumente, `.polaris/`. ZIP mit Rechten
0600, atomar geschrieben.

Bewusst getrennt vom Maschinengedaechtnis (`~/.config/ai-agents`): Der Curator
schreibt nie dorthin, und Maschinenzustand gehoert nicht in einen
Projektkontext - deshalb enthielt das Maschinengedaechtnis-ZIP kein
Projektwissen.

## Nachkuratierung der Einstufung (seit 2026-09-25)

```bash
polaris-curator reclassify Firma           # Plan erzeugen -> .polaris/reclassify-plan.json
polaris-curator reclassify Firma --apply   # genau diesen Plan anwenden (Backup vorher)
```

Seit Promptregel 2a ("KI-Assistent ist nie Stakeholder") stufen neue Laeufe
Antworten von ChatGPT/Claude anders ein; bestehende Eintraege aendert ein
normaler Lauf nicht. `reclassify` nimmt nur `[Stakeholder-Aussage]`-Eintraege
mit ausschliesslich Chat-Provenance, laesst das Modell einen Plan liefern und
prueft jede Aenderung lokal: bekannte ID, gueltige Werte, Scope/Evidenzgrad
steigen nicht, keine Zahl des alten Texts geht verloren. Unzulaessige
Aenderungen werden einzeln verworfen. `--apply` wendet nur den gespeicherten
Plan an und verweigert, wenn sich `knowledge.md` seitdem geaendert hat;
Provenance bleibt unveraendert, Backup unter `.polaris/backups/`.

## Projekttrennung und Ruecknahme (seit 2026-09-29)

Anlass: Am 2026-09-29 exportierte die Extension nach einem Chatwechsel die
noch sichtbaren Nachrichten des alten Chats unter der neuen Adresse; die Nachrichten
zweier Kundenprojekte landeten im jeweils anderen Projekt. Seither: Erfassung
als Chat-Abbild (Projekt aus den Anbieterdaten, siehe Haupt-README), die
Bridge wehrt eine schon anderswo archivierte Nachricht ab, der Curator sperrt,
und Ruecknahme ist moeglich.

**Sperre (`integrity.py`):** Vor jedem Lauf prueft der Curator lesend alle
`raw/*/messages/` der bekannten Projekte. Blockierend (Projekt wird **nicht
kuratiert**, `aborted_reason=integrity`, Health-Zeile „Projekttrennung“ rot):
`duplicate_id` (dieselbe ID in mehreren Projekten/Chats), `foreign_project`,
`url_mismatch` und `bridge_conflict` (Datei in
`~/.local/share/polaris/conflicts/` - sperrt **beide** Projekte, weil die
zuerst archivierte Kopie die falsche sein kann). Nicht blockierend, gelb:
`index_mismatch`, `unreadable`, `unknown_project`.

**Konflikt klaeren:** `polaris conflicts` listet, `polaris conflicts
--resolve <Datei> --keep existing|rejected` loest: die verworfene Kopie
wandert in `~/.local/share/polaris/quarantine/conflicts-<Datum>/`, bei
`rejected` wird die abgewehrte Kopie regulaer abgelegt. War die falsche Kopie
schon kuratiert, nennt die Ausgabe den `retract`-Befehl. Konfliktdateien
nicht einfach loeschen - das koennte die richtige Kopie verlieren.

**Ruecknahme (`retract`):**

```
polaris-curator retract Kunde-B --from-dir ~/.local/share/polaris/quarantine/X/Kunde-B          # anzeigen
polaris-curator retract Kunde-B --from-dir ... --apply              # nur reine Eintraege entfernen
polaris-curator retract Kunde-B --from-dir ... --apply --rebuild    # betroffene Batches neu kuratieren
```

Herkunftsangaben nennen alle Nachrichten eines Tagesbatches, eine
eingeschleppte Nachricht steckt also meist mit echten im selben Eintrag.
`--apply` entfernt nur Eintraege, deren Quellen ausschliesslich
zurueckgenommene Nachrichten sind. `--rebuild` entfernt alle Eintraege, die
eine Nachricht der betroffenen Batches nennen (transitiv ueber verdichtete
Eintraege), und markiert deren saubere Nachrichten im State als
unverarbeitet; der naechste Lauf baut das Wissen daraus neu auf.
Eintraege mit zusaetzlicher Dokumentquelle bleiben stehen und werden zur
Handpruefung gemeldet. Zurueckgenommene IDs bleiben als verarbeitet
markiert. IDs, die noch im Rohbestand dieses Projekts liegen, werden ohne
`--force` ausgelassen (Schutz davor, den Quarantaene-Ordner des *anderen*
Projekts zu uebergeben). Vorher Backup unter `.polaris/backups/`, entfernte
Eintraege woertlich in `.polaris/retracted/<zeit>.md`.

## Abdeckung, Journal, Robustheit (seit 2026-09-29)

- **Jeder Text eines Chats wird genau einmal kuratiert** (`chatgpt_source.
  load_new_messages`): ein Absatz (normalisiert, >= 20 Zeichen), der schon in
  einer kuratierten Fassung desselben Chats steht, geht nicht noch einmal an
  das Modell - egal unter welcher ID, ueber welchen Erfassungsweg oder in
  welcher Formatierung er kommt. Vollstaendig abgedeckte Nachrichten werden
  ohne Modellaufruf vermerkt; Teilstuecke mit Chat-Titel und vorangehender
  Frage. Zurueckgenommene Nachrichten (`retracted_ids`) nie wieder; Turns
  verworfener Zweige (`active_message_ids` aus dem Chat-Abbild) werden
  uebergangen.
- **Write-Ahead-Journal** (lokal unter `~/.local/state/polaris/journals/`,
  bis 2026-10-10 `.polaris/journal.json` auf dem Drive-Mount - dort ging das
  Loeschen verloren und ein Rest sperrte das Projekt; ein Journal am alten Ort
  wird weiter vollendet bzw. verworfen): Zielzustand aller
  Dateien und State-Aenderung vor dem Schreiben; ein unterbrochener Schritt
  wird beim naechsten Lauf vollendet. Passt eine Datei nicht mehr (jemand hat
  sie anders geaendert) oder ist das Journal beschaedigt, wird das Projekt
  gesperrt und im Health Check rot („Kurationsschritte“).
  `polaris-curator journal <Projekt>` zeigt den Stand, `--discard` verwirft
  ihn (Backup; die Quellen werden neu kuratiert). compact/reclassify/retract
  verweigern bei offenem Journal.
- **Warteschlange:** erste Einheit eines Laufs darf groesser als das Budget
  sein; wiederholt scheiternde Einheiten ruecken 24 h nach hinten (Warnung ab
  2); Fehler eines Projekts halten run-all nicht auf (Mount-Fehler: Exit 75).

## Eintragsnummern, Ablösung, Herkunft je Aussage (seit 2026-09-29)

Drei erprobte Konzepte aus LLM-Gedaechtnis-Systemen, ohne neue Abhaengigkeit
im bestehenden Markdown umgesetzt:

1. **Nummern statt Textmarken.** Jeder Eintrag traegt eine sichtbare,
   stabile Nummer: `ID: K12` (knowledge.md, Archiv), `**ID** D3`
   (decisions.md), `ID: L7` (open-loops.md). Das Modell aendert ueber die
   Nummer (`replaces_id`, `decision_id`, `loop_id`) statt ueber einen
   Textausschnitt, den es wiederfinden muesste. Unbekannte Nummer: nichts
   wird geraten (Eintrag angehaengt bzw. Aenderung uebergangen, Warnung).
   Nummern werden nie wiederverwendet (hoechste je gesehene + 1, inkl.
   Verweisen und Zaehlerzeile `<!-- polaris-ids: K=.. -->` im Dateikopf);
   zusammengefuehrte Eintraege behalten die erste Nummer und vermerken die
   uebrigen (`Zusammengeführt: K7`). Als Nummer gilt nur die eigene
   Unterzeile `  - ID: K12`, nie Fliesstext oder Code. Eintraege ohne Nummer
   werden bei jedem Lauf nach der Integritaetspruefung nummeriert - nur
   Zeilen eingefuegt, ueber das Journal, Backup `*-before-ids`; doppelte
   Ueberschriften in decisions.md werden zu "… (2)".
2. **Gültig seit / Abgelöst durch.** Neue Eintraege tragen `Gültig seit`.
   Ersetzt ein Eintrag einen anderen, wandert der alte mit „Abgelöst durch
   K40 am …“ nach `knowledge-archiv.md` (nicht ueberschrieben); der neue
   vermerkt `Ersetzt: K12` und behaelt dessen Herkunft. Eine schon
   abgeloeste Nummer fuehrt ueber die Kette `Ersetzt:` zur aktuellen
   Fassung. Entscheidungen bekommen `**Ersetzt durch**`, erledigte Punkte
   „erledigt am …“.
3. **Herkunft je Aussage.** Jeder Eintrag nennt `source_message_ids` - die
   Nachrichten, auf die er sich stuetzt. In die Herkunftsangabe kommen nur
   IDs aus dem aktuellen Batch; fehlt die Angabe oder passt keine, gilt wie
   frueher der ganze Batch (nie ein Eintrag ohne Herkunft). Nennt der
   Eintrag nur einen Teil, steht zusaetzlich `batch_ids=` (ganzer Batch).
   Fuer die Kundentrennung zaehlt immer der ganze Batch: `retract` entfernt
   ohne `--rebuild` nur ganz zurueckgenommene Batches und zeigt die Angabe
   des Modells nur als Hinweis; `--rebuild` entfernt alles, was den Batch
   gesehen hat, und baut den sauberen Teil neu auf.

## Verdichtung von knowledge.md (seit 2026-09-25)

```bash
polaris-curator compact <Projekt> --dry-run --show-plan   # Plan ansehen
polaris-curator compact <Projekt>                          # anwenden
```

Das Modell bekommt die nummerierten Einträge (ohne Provenance) und liefert
nur einen Plan: echte Dubletten mit zusammengeführter Fassung, sowie durch
neuere Einträge überholte Einträge. Der Plan wird lokal geprüft und
deterministisch angewendet; Provenance bleibt vollständig erhalten,
Klassifikationen werden nie aufgewertet, überholte Einträge wandern nach
`knowledge-archiv.md` (nichts wird gelöscht), vorher Backup unter
`.polaris/backups/`. Notbremse: schrumpft die Datei um mehr als die Hälfte,
wird der Plan verworfen. Automatisch im Timer-Lauf ab
`compaction.threshold_chars` (Default 100000, 0 = nie), höchstens alle
`compaction.min_interval_days` (Default 7) Tage.

**Abstand nach ergebnislosen Läufen (seit 2026-10-17):** Findet eine
Verdichtung nichts (keine Zusammenführung, nichts überholt) bzw. schließt die
wöchentliche Prüfung offener Punkte (`compaction.loops_review_interval_days`)
keinen Punkt, verdoppelt sich der Abstand bis zum Faktor
`compaction.idle_backoff_max` (Default 4: 7 → 14 → 28 Tage; 1 = aus). Ein
Lauf mit Ergebnis setzt zurück; ist `knowledge.md` seit der letzten
Verdichtung um 10 % gewachsen, gilt für sie wieder der Grundabstand. Ein
fehlgeschlagener Lauf ändert den Zähler nicht. Zähler `idle_streak` in
`.polaris/compact-state.json` bzw. `loops-review.json`. Anlass: Audit
2026-10-17, die letzten Läufe aller Projekte schlossen 0 Punkte und fanden
fast keine Dubletten (je Aufruf 80–350k Zeichen Eingabe).

## Schutz vor Verlust und Vermischung (seit 2026-09-29, Architektur-Review)

- **Nur zugeordnete Nachrichten:** Kuratiert wird nur, was laut
  `projects.yaml` (status `ok`/`missing_target`) genau diesem Ordner
  zugeordnet ist. Nachrichten mit fremder oder unbekannter `project.id`
  (z. B. nach `polaris unmap`) bleiben unverarbeitet liegen; der Lauf warnt,
  der Health Check zeigt sie. Ohne jede Zuordnung (frische Installation,
  Tests) wird nicht gefiltert.
- **Nachtraege:** Eine neue Fassung einer schon kuratierten Nachricht wird
  nur still uebernommen, wenn kein neuer Absatz mit >= 20 Zeichen
  dazukommt und alles Neue zusammen < 100 Zeichen hat.
- **Verdichtung:** Originale zusammengefuehrter Eintraege wandern woertlich
  ins Archiv; eine Zusammenfuehrung, deren Fassung eine Zahl der Originale
  verliert, wird verworfen (Ausgabe "verworfen …"). Geschrieben wird ueber
  das Journal. Faellig spaetestens bei 80 % von `max_curated_context_chars`.
- **Gekuerzt/Kontextgrenze:** Wird ein Dokument gekuerzt oder ist eine
  kuratierte Datei groesser als der Kontext, den das Modell sieht, meldet
  der Lauf das ("gekuerzt kuratiert", "Kontextgrenze").
- **Drive-Mount:** Unter `project_roots` (`~/.config/polaris/config.yaml`)
  legt der Curator nie einen Projektordner an; fehlt er, endet der Lauf mit
  Exit 75 (spaeter erneut) statt Schattenordner auf der lokalen Platte.
- **Laufergebnis:** `run`/`run-all` schreiben `.polaris/last-run.json`
  (Fehler, wichtige Warnungen, Rueckstand). Das Widget zeigt es unter
  Curator > Kuration (gelb bei Fehlern, wichtigen Warnungen, wiederholt
  scheiternden Einheiten, Rueckstand > 7 Tage).
- **Codex-Fehler:** Im Fehlertext stehen nur die `ERROR`-Zeilen von codex
  (kein Prompt-Echo mit Kundeninhalt); ein Nutzungslimit wird als solches
  benannt. Neuversuche alle 20 min bis ~8 h (Unit: 12 h / 25 Starts).

## Claude: Ersatzmodell und unabhaengige Pruefung (seit 2026-09-30)

- `claude_fallback_model` (z. B. `opus`): ist Codex nicht erreichbar (Limit,
  Ausfall), kuratiert direkt Claude ueber `claude -p` (abgeschottet: keine
  Werkzeuge, keine Einstellungen/CLAUDE.md, keine Sitzung). Nur bei Prompts
  mit Bildern bleibt das zweite Codex-Modell (`fallback_model`).
- `reviewer_model` (z. B. `opus`): Claude prueft jeden neuen Eintrag gegen
  den ganzen Quellinhalt (bei Abloesungen mit dem alten Eintrag). Nichts
  wird verworfen; schwere Beanstandungen stehen als "Prüfhinweis" am
  Eintrag und im Laufergebnis, alle Befunde in `.polaris/review-findings.json`.
  Ohne Pruefung: Dokumente mit Bildern, von Claude selbst kuratierte Batches,
  und nach einem Ausfall des Pruefers der Rest des Laufs (gemeldet).

## Grosse Dokumente und doppelte Anhaenge (seit 2026-10-07)

- Limit je Dokument = `max_input_chars_per_batch` (hoechstens das Laufbudget).
- Bis `document_oversize_factor` (Standard 2) mal dem Limit geht ein Dokument
  ausnahmsweise in **einem** Aufruf durch: guenstiger als Teile, weil das
  Projektwissen nur einmal mitgeschickt wird.
- Darueber wird es in **Teile** zur Groesse des Limits zerlegt (an Zeilen-
  enden), Teil fuer Teil kuratiert; jeder Teil sieht das Wissen aus den
  vorherigen. Das Budget pro Lauf gilt weiter, Restteile kommen im naechsten
  Lauf. Fortschritt: `document_parts` im Zustand; archiviert wird erst nach dem
  letzten Teil. Hoechstens `max_parts_per_document` (Standard 5) Teile, der
  Rest wird gekuerzt und als "gekuerzt kuratiert" gemeldet - dieser Hinweis
  erscheint nur noch fuer wirklich kuratierte Dokumente.
- Mail-Anhaenge, die byte-gleich als eigenes Dokument in `inbox/documents`
  bzw. `inbox/notes` liegen oder schon kuratiert sind, werden nur abgelegt und
  verwiesen ("identisch mit …, dort ausgewertet") statt doppelt kuratiert.

```yaml
budget:
  max_parts_per_document: 5        # 1 = nie teilen
  document_oversize_factor: 2.0    # 1.0 = nie einmalig groesser
```

## Budgetschutz

Harte Limits pro Lauf (siehe Config oben). Wird ein Limit erreicht, endet
der Lauf sauber, verbleibende Batches/Dokumente bleiben offen und werden
beim naechsten Lauf (naechster Timer-Tick oder manueller Aufruf)
weiterverarbeitet - aeltester offener Tagesbatch zuerst. Kein Batch wird
als verarbeitet markiert, wenn der zugehoerige LLM-Call oder Merge nicht
vollstaendig erfolgreich war.

Ist das primaere Modell nicht erreichbar, wird genau einmal das
Fallback-Modell versucht; schlaegt auch das fehl, bricht der gesamte Lauf
sauber ab (nichts wird als verarbeitet markiert, der naechste Lauf
wiederholt automatisch). Eine ungueltige Modellantwort (Format-/
Schema-Fehler) betrifft dagegen nur den einzelnen Batch - der Lauf faehrt
mit den uebrigen Einheiten fort.

## Tagesbatching

Neue ChatGPT-Messages werden pro Projekt und lokalem Kalendertag
gruppiert (Zeitanker: `captured_at`, da `source_timestamp` in der
aktuellen Extension-Version immer `null` ist). Zu grosse Tage werden in
Sub-Batches geteilt (`2026-09-12#1`, `2026-09-12#2`, ...), wobei
zusammenhaengende Conversation-Bloecke nach Moeglichkeit nicht
auseinandergerissen werden. Ein Tag ist nie endgueltig "abgeschlossen" -
neue Messages desselben Tages werden beim naechsten Lauf automatisch als
Nachtrag erkannt (State basiert auf Message-IDs, nicht auf dem Datum
allein).

## Catch-up nach mehrtaegiger Pause

Beim naechsten Lauf werden alle seit dem letzten Erfolg offenen
Tagesbatches erkannt und chronologisch (aeltester zuerst) innerhalb des
Budgets abgearbeitet. Reicht das Budget nicht fuer alle offenen Tage,
verarbeitet der naechste Lauf die restlichen - keine Ueberschreitung des
Budgets nur wegen Rueckstand.

## Qualitaetslogik (v0.2)

`change_schema.py` erzwingt pro `knowledge_updates`-Eintrag eine
Klassifikation, statt sie dem Modell freizustellen (`prompts.py`
begruendet und illustriert jede Regel im Detail):

- `entry_type`: `fact` (belegte Beobachtung) / `stakeholder_claim`
  (Wahrnehmung/Aussage einer Person - keine bestaetigte Tatsache) /
  `hypothesis` (analytische Ableitung, muss als Vermutung markiert sein)
  / `implication` (Bedeutung fuer Operating Model, Fuehrung, Skalierung,
  Kundenbindung, Wirtschaftlichkeit). Echte Entscheidungen gehoeren
  separat in `decisions_add`.
- `scope` (`local`/`functional`/`cross_functional`/`company_wide`) +
  `evidence_strength` (`single_source`/`multiple_sources`/`corroborated`)
  + `confidence` verhindern, dass ein Einzelfall ("das ist ueberall so")
  direkt als unternehmensweites Muster gespeichert wird - Hochstufen nur
  bei tatsaechlich mehreren unabhaengigen Quellen.
- `causal_chain` (nur bei `hypothesis`) erfasst vermutete
  Ursache-Wirkungs-Ketten (z.B. fehlende interne Bearbeitung ->
  persoenliche Kompensation -> Belastung -> Key-Person-Risiko ->
  Mandatsrisiko) explizit als Hypothese, nie als Fakt.
- `key_person_dependency` erfasst gezielt Kompensationsmuster (Kunden
  bleiben wegen einer Person, ein Prozess laeuft nur durch persoenliches
  Nachhalten, Eskalationen laufen wiederholt ueber dieselbe Person).
- Founder-/Management-Bottlenecks: eine Eskalation an eine einzelne
  Fuehrungsperson wird als kurzfristige Intervention (`decisions_add`,
  meist `decision_scope=local`/`provisional`) UND separat als
  `open_loops_add` mit `loop_type=leadership_question` erfasst (wie
  saehe der dauerhafte, skalierbare Mechanismus aus?) - nie als bereits
  geloeste Prozessfrage.
- `decisions_add.validated` muss bei einer neuen Entscheidung `false`
  sein (strukturell erzwungen, siehe `change_schema.py`) - eine
  eingefuehrte Massnahme gilt nicht automatisch als wirksam.
  `decisions_update` kann `validated`/`validation_note` spaeter setzen,
  wenn eine Folgequelle das tatsaechlich belegt.
- `open_loops_add.loop_type` kategorisiert offene Punkte (Datenbedarf,
  Fuehrungsfrage, Wirksamkeitspruefung, Prozess-/Verantwortungsluecke,
  wirtschaftliche Frage) statt eines reinen To-do-Archivs.
- Abgleich mit bestehendem Wissen: der Prompt bekommt den aktuellen
  Stand aller drei Dateien als Kontext und wird angewiesen, eine
  bestaetigende/praezisierende neue Quelle ueber `mode=replace` +
  `replace_marker` (plus `related_existing_insight`) in den bestehenden
  Eintrag zu verdichten (z.B. Evidenzgrad hochstufen), statt einen neuen,
  fast identischen Eintrag anzuhaengen. `merge.py` ersetzt dabei den
  GANZEN alten Eintrags-Block (Klassifikation + Meta-Zeilen +
  Provenance), nicht nur die erste Zeile - sonst blieben alte
  Meta-Zeilen verwaist stehen.
- `self_review` (Pflichtfeld am Ende der Modellantwort): 9 boolesche
  Felder, die exakt die Kritik-Checkliste aus der Aufgabenstellung
  abbilden (Stakeholder-Aussage faelschlich als Fakt? Einzelfall
  ueberverallgemeinert? Intervention als bewiesen dargestellt?
  systemische Implikation uebersehen? Key-Person-Abhaengigkeit
  uebersehen? Kunden-/wirtschaftliche Wirkung uebersehen?
  Fuehrungsperson als dauerhafter Prozessersatz behandelt? langfristig
  relevant/nicht redundant? Aussage geht nicht ueber die Quelle hinaus?).
  `pipeline.py` verwirft den GESAMTEN Batch lokal (kein Merge, Batch gilt
  als nicht verarbeitet, wird beim naechsten Lauf erneut versucht), wenn
  auch nur ein Feld `false` ist - der Prompt sagt dem Modell das explizit,
  um einen Anreiz zu setzen, vor dem Antworten tatsaechlich zu korrigieren
  statt nur zu behaupten.

**Wichtige Einschraenkung**: `self_review` ist eine Selbstauskunft
desselben Modellaufrufs, kein unabhaengiger zweiter Blick. Sie zwingt das
Modell, die Kritik-Kriterien als Pflichtfelder explizit "durchzudenken"
(mehr erzwungene Zwischenschritte vor der finalen Antwort), ersetzt aber
keine echte, vom Erzeuger unabhaengige Zweitpruefung. Ob das ausreicht
oder ein echter zweiter, unabhaengiger Kritik-Call noetig ist, sollte
anhand weiterer reproduzierter Faelle (nicht nur des einen
RPS/Schkeuditz-Testlaufs) neu bewertet werden - siehe "Modellstrategie".

## Modellstrategie

Bewusst NICHT geaendert in v0.2: weiterhin `gpt-5.6-luna` (guenstig,
laut Codex-Katalog fuer agentisches Coding, nicht spezifisch fuer tiefes
analytisches Reasoning optimiert) in einem einzelnen `codex exec`-Call
pro Batch/Dokument, der Extraktion UND Klassifikation UND
Merge-Entscheidung in einem Schritt erledigt.

Begruendung: Der urspruengliche Qualitaetsverlust im RPS/Schkeuditz-
Testlauf war ueberwiegend WORKFLOW-/SCHEMA-bedingt (keine erzwungene
Klassifikation, kein Scope/Evidenzgrad, keine Selbstkritik, keine
Anleitung zu Wirkungsketten/Key-Person-Mustern), nicht primaer
Modell-bedingt - dieselbe Aufgabenstellung fordert deshalb explizit,
Prompting/Workflow/Datenmodell zuerst zu verbessern und erst danach zu
pruefen, ob verbleibende Probleme tatsaechlich am Modell liegen. Ein
ungeprueft groesseres/teureres Modell haette dieses Risiko verdeckt statt
behoben.

Falls der Regressionstest (unten) nach dieser Aenderung weiterhin
systematische Schwaechen zeigt, die eher nach Reasoning-Limit als nach
Prompt-/Schema-Luecke aussehen (z.B. wiederholt uebersehene
Wirkungsketten trotz expliziter Anleitung), ist die im Auftrag
vorgeschlagene Zwei-Stufen-Architektur die naheliegende naechste
Eskalationsstufe: ein guenstiges Modell fuer reine
Parsing-/Extraktionsschritte, ein staerkeres Reasoning-Modell NUR fuer
den Synthese-/Bewertungs-/Kontextabgleich-Schritt (zweiter `codex
exec`-Call mit dem extrahierten Rohmaterial + aktuellem Wissensstand als
Input). Das wuerde die Anzahl der LLM-Calls pro Einheit verdoppeln
(Budget-Konfiguration muesste entsprechend angepasst werden) und ist
DESHALB hier bewusst noch nicht implementiert, sondern als Option
dokumentiert.

## Dokumentquellen

Unterstuetzt: `.eml` (Header + Klartext-/HTML-Body + Anhaenge, siehe
unten), `.pdf` (via `pdftotext`, bereits als Systempaket vorhanden),
`.docx`, `.xlsx`/`.xlsm`, `.pptx` (nur Python-Standardbibliothek, siehe
`office_extract.py` - diese Formate sind ZIP-Archive mit XML), `.csv`,
`.md`, `.txt`. Alte Binaerformate (`.doc`, `.xls`, `.ppt`) und `.msg`
werden nicht unterstuetzt; solche Dateien in der Inbox werden bei jedem
Lauf als Warnung ins `curator.log` geschrieben statt still uebergangen.
Aenderungserkennung ueber SHA-256-Hash der Datei - eine unveraenderte Datei
wird nie erneut analysiert. Nach erfolgreichem LLM-Aufruf, validiertem
Merge und lokaler Verarbeitung werden Quelldateien nach
`inbox/<typ>/archive/processed/` verschoben. Nicht lesbare Dateien sowie
Quellen mit LLM-, Schema-, Merge- oder Archivierungsfehlern bleiben in der
Inbox. Bereits vorhandene Archivdateien werden nie ueberschrieben; bei
Namenskollisionen erhaelt die neue Datei einen kurzen Inhalts-Hash.

Der Text eines Dokuments (inkl. Anhaenge) wird auf den kleineren Wert von
`budget.max_input_chars_per_batch` und `budget.max_total_input_chars_per_run`
gekappt (mit sichtbarem
Abschneide-Hinweis im Prompt). Vorher konnte ein einzelnes Dokument, das
groesser als das Laufbudget war, die Warteschlange dauerhaft blockieren.

### Mail-Anhaenge (seit 2026-09-24)

- **Text-Anhaenge** (PDF, DOCX, XLSX, PPTX, CSV, TXT, MD) werden
  extrahiert und im Prompt als Abschnitt `=== ANHAENGE ===` an den
  Mailtext angehaengt (pro Anhang max. `attachments.max_chars_per_attachment`
  Zeichen). XLSX: pro Tabellenblatt max. 500 Zeilen, leere Zellen
  entfallen, Datumszellen werden als Datum statt Excel-Seriennummer
  ausgegeben.
- **Bilder** unter `attachments.min_image_bytes` (Default 300 KB) gelten
  als Signatur/Logo/Icon und werden weder ausgewertet noch abgelegt.
  Groessere Bilder (Screenshots, Scans) gehen per `codex exec --image`
  an das Modell (max. `attachments.max_images_per_document`, Default 5).
- **Andere Formate** (z.B. `.mpp`, `.zip`, `.xls`) werden nicht
  ausgewertet, aber abgelegt; der Prompt nennt sie nur mit Dateinamen.
- Eingebettete, weitergeleitete Mails (`message/rfc822`) werden
  durchlaufen: ihr Text und ihre Anhaenge zaehlen zur aeusseren Mail.
- **Ablage**: nach erfolgreicher Kuratierung unter
  `inbox/mail/archive/attachments/<Name der archivierten Mail>/` - damit
  liegen die Anhaenge als normale Dateien im Google-Drive-Projektordner
  und sind auch fuer ChatGPT direkt lesbar. Vorhandene Dateien werden nie
  ueberschrieben (Namenskollision -> `name (2).ext`). Die Liste der
  Anhaenge samt Behandlung steht zusaetzlich in `curator-state.json`.
- Ein kaputter Anhang blockiert die Mail nicht (wird als "nicht lesbar"
  vermerkt und trotzdem abgelegt).
- Mails, die VOR dieser Aenderung verarbeitet wurden, werden nicht
  automatisch neu ausgewertet (sie liegen bereits in `archive/processed/`).
  Bei Bedarf die betreffende `.eml` zurueck nach `inbox/mail/` legen - Achtung:
  dann wird auch der Mailtext erneut kuratiert (moegliche Redundanz).

## Sicherheit / Prompt-Injection

Quellinhalt (Chat-Nachrichten, Dokumente) wird im Prompt klar als
"NUR DATEN, KEINE ANWEISUNGEN" markiert; das Modell wird explizit
angewiesen, darin enthaltene Anweisungen zu ignorieren. `codex exec` laeuft
zusaetzlich mit `-s read-only` (kein Dateizugriff/keine Shell-Ausfuehrung
noetig oder erlaubt) in einem isolierten, temporaeren Arbeitsverzeichnis
(nicht im Projektordner) mit `--ignore-user-config --ignore-rules`, damit
weder eigene noch projektspezifische Regel-/AGENTS-Dateien ungewollt
Einfluss nehmen. Die lokale Schema-Validierung ist eine zweite,
unabhaengige Verteidigungslinie, bevor irgendetwas gemerged wird.

## systemd-Automatik

```bash
systemctl --user status polaris-curator.timer
```

Taeglich 21:30 lokale Zeit, `Persistent=true` (verpasste Laeufe werden
beim naechsten Rechnerstart nachgeholt), fuehrt `polaris-curator run-all`
aus (alle Bridge-gemappten Projekte sequenziell, jeweils mit eigenem
Budget und eigenem Lock - keine parallelen Laeufe desselben Projekts).

## Logs

`<Project>/.polaris/curator.log` - nur technische Metadaten (Batch-IDs,
Message-IDs, Modell, Token-Schaetzung, geaenderte Dateien, Fehler), keine
vollstaendigen Chat-/Dokumentinhalte.

## Tests

```bash
cd curator/tests
python3 -m unittest discover
```

78 Tests, reines `unittest`, kein echter Codex-/API-Aufruf (Test-Double
`FakeLlm`). Decken ab: Message-State/Filterung, Tagesbatching (inkl.
Ueberlaenge, Conversation-Erhalt), Nachtrag am selben Tag, mehrtaegigen
Catch-up, Budgetgrenzen, Restart nach fehlgeschlagenem Lauf,
Dokument-Hashing (inkl. echtem `.pdf` ueber `pdftotext`), Schema-
Validierung (inkl. Taxonomie-Enums, `validated=false`-Zwang,
Selbstkritik-Pflichtfelder), Merge (Append/Replace-ganzer-Block/
Konflikt/Open-Loop-Close/Klassifikations-Rendering), Selbstkritik-Gate
in der Pipeline (verworfener Batch bei `false`-Flag, erfolgreicher
Retry im naechsten Lauf), Dry-Run, Idempotenz.

## ChatGPT-Projektanweisung

Siehe `chatgpt-project-instruction.md` fuer einen Empfehlungstext, den du
bei Bedarf manuell in die ChatGPT-Project-Instructions einfuegen kannst.
Wird nicht automatisch angewendet.

## Bekannte Grenzen (v0.2)

- Kein automatischer Model Resolver - Modell-IDs muessen bei Bedarf
  manuell in `curator.yaml` aktualisiert werden, wenn sich Codex' Katalog
  aendert.
- `min_messages_for_early_run`/`min_chars_for_early_run` sind aktuell
  wirkungslos (siehe Konfigurationsabschnitt oben).
- `decisions_update`/`open_loops_close`-Matching ist Substring-basiert
  (einfach, aber bei sehr aehnlichen Titeln/Formulierungen theoretisch
  mehrdeutig - im Zweifel wird der letzte Treffer verwendet und dies
  ist nicht separat geloggt).
- `replace_marker`/`related_existing_insight`-Matching (fuer Verdichtung
  statt Archiv-Wachstum, siehe "Qualitaetslogik") ist ebenfalls
  Substring-basiert und haengt vollstaendig davon ab, dass das Modell im
  Kontext tatsaechlich einen passenden bestehenden Eintrag erkennt - es
  gibt keine unabhaengige semantische Duplikaterkennung (kein Embedding-
  Vergleich). Bei sehr grossen `knowledge.md`-Dateien wird der Kontext
  zudem am Ende auf `max_curated_context_chars` gekappt (von hinten
  gekuerzt) - aeltere Eintraege koennen dann aus dem Abgleich
  herausfallen.
- `self_review` ist eine Selbstauskunft desselben Modellaufrufs, kein
  unabhaengiger zweiter Blick - siehe Einschraenkung im Abschnitt
  "Qualitaetslogik" und die Ueberlegung zu einem echten zweiten
  Kritik-Call in "Modellstrategie".
- Die neue Klassifikation/Selbstkritik erhoeht die Anforderungen an das
  Modell pro Call (mehr Pflichtfelder, mehr Bedingungen), ohne die
  Token-/Kosten-Struktur zu aendern (weiterhin 1 Call pro Batch/
  Dokument) - ob `gpt-5.6-luna` das zuverlaessig genug leistet oder ein
  staerkeres Modell fuer den Synthese-Anteil noetig ist, ist erst nach
  mehreren realen Laeufen (nicht nur dem einen RPS/Schkeuditz-
  Regressionstest) wirklich zu beurteilen.
- Kein automatisches Zusammenfassen/Verdichten von `knowledge.md`
  ueber die Zeit hinweg (jenseits des gezielten `mode=replace` pro
  Eintrag) - bei sehr vielen bestaetigten Hypothesen kann die Datei
  langfristig trotzdem wachsen, wenn das Modell einen Bezug nicht
  erkennt.
