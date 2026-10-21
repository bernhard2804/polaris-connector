# Maschinengedächtnis — Index

<!-- Vorlage aus dem Polaris-Installationspaket. -->

Gemeinsames Gedächtnis dieses Rechners, das alle lokalen Agenten lesen und schreiben. Es besteht aus reinen Markdown-Dateien und ist mit jedem Editor lesbar. Diese Datei ist der **Index**.

## So wird das Gedächtnis benutzt

Pro Thema gibt es bis zu drei Dateien mit gleichem Namen:

| Schicht | Inhalt | Wann lesen |
|---|---|---|
| `handoff/<thema>.md` | **aktueller** Stand, Leitplanken, offene Punkte, Verweise (klein halten, 3–8 KB) | immer, wenn das Thema berührt wird |
| `knowledge/<thema>.md` | dauerhaftes Wissen; Überholtes als „Veraltet: …“ mit „Ersetzt durch: …“ | bei Änderungen am Thema |
| `history/<thema>.md` | abgeschlossene Episoden, wörtliche Chronik | gezielt suchen, wenn Hintergrund nötig ist |

1. **Lesen:** Diese Datei ganz lesen, dann den passenden Handoff.
2. **Schreiben:** Stand im Handoff aktualisieren, Dauerhaftes nach `knowledge/`, die Episode nach `history/`. Details: `AGENTS.md` → „Maschinengedächtnis“.
3. **Prüfen:** `python3 ~/.config/ai-agents/tools/memlint.py` (Exit 0 = in Ordnung).
4. **Index pflegen:** Ändert sich der Status eines Themas oder entsteht eine neue kritische Regel, im selben Arbeitsgang die Tabellenzeile bzw. „Kritische Regeln“ anpassen.

## Kritische Regeln (immer befolgen)

- **Dokumentationspflicht:** Persistente Eingriffe sind erst abgeschlossen, wenn sie dokumentiert und gegen den Ist-Zustand geprüft sind (Details in `AGENTS.md`).
- **Keine Geheimniswerte im Gedächtnis**, auch nicht indirekt.

## Maschine

- (Modell, Betriebssystem, Besonderheiten hier eintragen.)

## Themen

| Datei | Thema | Status |
|---|---|---|
