"""Kuratiertes Wissen aus bestimmten Nachrichten zuruecknehmen (seit 2026-09-29).

Anlass: Am 2026-09-29 lagen Nachrichten zweier Kundenprojekte im jeweils anderen Ordner.
Der Curator war zufaellig noch nicht gelaufen. Waere er es, haette man die
daraus entstandenen Eintraege in knowledge.md usw. von Hand suchen muessen.

Grundlage ist die Herkunftsangabe jedes Eintrags
(<!-- polaris-source: ... message_ids=a,b,c ... -->). Sie nennt alle
Nachrichten des Tagesbatches, aus dem der Eintrag entstand. Eine
eingeschleppte Nachricht steckt deshalb meist im selben Batch wie echte, und
ein Eintrag laesst sich nicht sicher einer einzelnen Nachricht zuordnen.
Daher zwei Stufen:

- apply:           nur Eintraege entfernen, deren saemtliche Quellen
                   ausschliesslich aus zurueckgenommenen Nachrichten bestehen.
- apply + rebuild: alle Eintraege der betroffenen Batches entfernen (inkl.
                   Batches, die per Verdichtung mit ihnen verschmolzen sind)
                   und deren saubere Nachrichten im State wieder als
                   unverarbeitet markieren. Der naechste Curator-Lauf baut das
                   Wissen dann aus sauberen Daten neu auf, ohne Dubletten.
                   Eintraege, die zusaetzlich auf einem Dokument beruhen,
                   werden nicht entfernt, sondern zur Handpruefung gemeldet
                   (Dokumente werden nicht neu eingestellt).

Sicherheitsprinzipien: Ohne apply wird nichts geschrieben. Mit apply wird
vorher jede geaenderte Datei unter .polaris/backups/ gesichert, und alle
entfernten Eintraege werden woertlich nach .polaris/retracted/<zeit>.md
geschrieben. Zurueckgenommene Nachrichten bleiben im State als verarbeitet
markiert, damit sie nie (wieder) kuratiert werden.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import layout, merge, state as state_mod, storage
from .compact import ARCHIVE_NAME

_SOURCE_RE = re.compile(r"<!-- polaris-source:(.*?)-->")
_IDS_RE = re.compile(r"\bmessage_ids=(\S+)")
_BATCH_RE = re.compile(r"\bbatch_ids=(\S+)")
_TYPE_RE = re.compile(r"\btype=(\S+)")

# Datei -> Art der Eintraege. "blocks": "- "-Eintraege je ##-Abschnitt
# (knowledge, Archiv, open-loops); "sections": ein ##-Abschnitt je Eintrag
# (decisions).
_FILES = (
    ("knowledge.md", "blocks"),
    (ARCHIVE_NAME, "blocks"),
    ("open-loops.md", "blocks"),
    ("decisions.md", "sections"),
)


class RetractError(Exception):
    pass


@dataclass
class _Entry:
    file: str
    section: str
    index: int  # Position des Blocks im Abschnitt (nur "blocks")
    text: str
    batches: list[frozenset[str]]  # je Chat-Herkunftsangabe der ganze Batch (batch_ids, sonst message_ids)
    other_sources: int  # Herkunftsangaben ohne message_ids (Dokumente)
    cited: list[frozenset[str]] = field(default_factory=list)  # je Angabe die laut Modell genutzten (message_ids)

    @property
    def label(self) -> str:
        first = self.text.strip().splitlines()[0] if self.text.strip() else ""
        return f"{self.file} › {self.section}: {first[:100]}"


@dataclass
class RetractResult:
    project: str = ""
    retracted_ids: int = 0
    found_in_sources: int = 0
    rebuild: bool = False
    written: bool = False
    full: list[str] = field(default_factory=list)  # nur aus zurueckgenommenen Nachrichten
    partial: list[str] = field(default_factory=list)  # gemischt, Batch betroffen
    manual: list[str] = field(default_factory=list)  # rebuild: bleibt stehen (Dokumentquelle o. nicht neu aufbaubar)
    not_rebuilt: list[str] = field(default_factory=list)  # entfernt, sauberer Anteil kommt NICHT automatisch zurueck
    removed: list[str] = field(default_factory=list)
    cited_only: list[str] = field(default_factory=list)  # stuetzt sich laut Angabe nur auf Zurueckgenommenes (-> --rebuild)
    still_in_raw: list[str] = field(default_factory=list)  # ausgelassen: liegt noch im Rohbestand
    requeued_ids: int = 0
    backups: list[str] = field(default_factory=list)
    retracted_file: str | None = None


def _split_ids(raw: str) -> frozenset[str]:
    return frozenset(i for i in raw.split(",") if i)


def _parse_sources(text: str) -> tuple[list[frozenset[str]], int, list[frozenset[str]]]:
    """Seit der Herkunft je Aussage nennt message_ids nur die Nachrichten, die
    das Modell als Grundlage angibt; batch_ids nennt den ganzen Batch, den es
    gesehen hat. Fuer die Kundentrennung zaehlt der ganze Batch (eine falsche
    Angabe des Modells darf Fremdinhalt nicht stehen lassen, Review
    2026-09-29, HIGH); die Angabe macht nur "rein zurueckgenommen" genauer."""
    batches: list[frozenset[str]] = []
    cited: list[frozenset[str]] = []
    other = 0
    for m in _SOURCE_RE.finditer(text):
        body = m.group(1)
        ids = _IDS_RE.search(body)
        if ids:
            batch = _BATCH_RE.search(body)
            c = _split_ids(ids.group(1))
            batches.append(_split_ids(batch.group(1)) | c if batch else c)
            cited.append(c)
        else:
            other += 1
    return batches, other, cited


def _blocks(body: str) -> list[list[str]]:
    """Wie merge._split_into_blocks, aber Zwischenueberschriften ("### ...",
    z. B. im Wissensarchiv) bilden eigene Bloecke. Sonst hing die Ueberschrift
    des naechsten Eintrags am vorherigen und verschwand mit ihm (Review
    2026-09-29)."""
    blocks: list[list[str]] = []
    for block in merge._split_into_blocks(body):
        current: list[str] = []
        for line in block:
            if line.startswith("#"):
                if current:
                    blocks.append(current)
                blocks.append([line])
                current = []
            else:
                current.append(line)
        if current:
            blocks.append(current)
    return blocks


def _duplicate_headings(text: str) -> list[str]:
    seen: set[str] = set()
    dups: list[str] = []
    for line in text.splitlines():
        m = merge.HEADING2_RE.match(line)
        if m:
            h = m.group(1).strip()
            if h in seen:
                dups.append(h)
            seen.add(h)
    return dups


def _entries_of(file: str, kind: str, text: str) -> list[_Entry]:
    _preamble, sections = merge.parse_level2_sections(text)
    entries: list[_Entry] = []
    for heading, body in sections.items():
        if kind == "sections":
            batches, other, cited = _parse_sources(body)
            entries.append(_Entry(file, heading, 0, f"## {heading}\n{body}", batches, other, cited))
            continue
        for i, block in enumerate(_blocks(body)):
            if not block or not block[0].startswith("- "):
                continue
            block_text = "\n".join(block)
            batches, other, cited = _parse_sources(block_text)
            entries.append(_Entry(file, heading, i, block_text, batches, other, cited))
    return entries


def _matches(ids: set[str], prefixes: list[str], candidate: str) -> bool:
    return candidate in ids or any(candidate.startswith(p) for p in prefixes)


def _normalize_ids(message_ids: list[str]) -> tuple[set[str], list[str]]:
    full: set[str] = set()
    prefixes: list[str] = []
    for raw in message_ids:
        mid = raw.strip()
        if not mid:
            continue
        if len(mid) < 8:
            raise RetractError(f"ID-Praefix zu kurz (mindestens 8 Zeichen): {mid}")
        if len(mid) >= 32:
            full.add(mid)
        else:
            prefixes.append(mid)
    if not full and not prefixes:
        raise RetractError("keine Nachrichten-IDs angegeben")
    return full, prefixes


def ids_from_dir(directory: Path) -> list[str]:
    """Nachrichten-IDs aller Event-Dateien eines Ordners (rekursiv), z. B.
    eines Quarantaene-Ordners."""
    result: list[str] = []
    for path in sorted(directory.rglob("*.json")):
        try:
            event = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        mid = (event.get("message") or {}).get("id") if isinstance(event, dict) else None
        if mid:
            result.append(mid)
    return result


def _remove_entries(kind: str, text: str, doomed: list[_Entry]) -> str:
    preamble, sections = merge.parse_level2_sections(text)
    if kind == "sections":
        for e in doomed:
            sections.pop(e.section, None)
        return merge.render_level2_sections(preamble, sections)
    by_section: dict[str, set[int]] = {}
    for e in doomed:
        by_section.setdefault(e.section, set()).add(e.index)
    for heading, indices in by_section.items():
        blocks = _blocks(sections.get(heading, ""))
        kept = [b for i, b in enumerate(blocks) if i not in indices]
        sections[heading] = merge._join_blocks(kept)
    return merge.render_level2_sections(preamble, sections)


def _raw_message_ids(project: Path) -> set[str]:
    ids: set[str] = set()
    for _src, directory in layout.chat_messages_dirs(project):
        for path in directory.glob("*.json") if directory.is_dir() else []:
            try:
                mid = (json.loads(path.read_text(encoding="utf-8")).get("message") or {}).get("id")
            except (json.JSONDecodeError, OSError, AttributeError):
                continue
            if mid:
                ids.add(mid)
    return ids


def retract(
    project: Path,
    message_ids: list[str],
    apply: bool = False,
    rebuild: bool = False,
    force: bool = False,
) -> RetractResult:
    """force: auch IDs zuruecknehmen, die noch im Rohbestand dieses Projekts
    liegen. Ohne force werden sie ausgelassen - eine Nachricht, die hier noch
    liegt, gehoert vermutlich legitim hierher (z. B. wenn der ganze
    Quarantaene-Ordner beider Projekte uebergeben wurde)."""
    full_ids, prefixes = _normalize_ids(message_ids)
    result = RetractResult(project=project.name, rebuild=rebuild)

    texts: dict[str, str] = {}
    entries: list[_Entry] = []
    for name, kind in _FILES:
        path = project / name
        if not path.is_file():
            continue
        texts[name] = path.read_text(encoding="utf-8")
        entries.extend(_entries_of(name, kind, texts[name]))

    # Tatsaechlich vorkommende, zurueckgenommene IDs (Praefixe aufgeloest).
    all_ids = {i for e in entries for b in e.batches for i in b}
    retracted = {i for i in all_ids if _matches(full_ids, prefixes, i)} | full_ids
    if not force:
        present = retracted & _raw_message_ids(project)
        result.still_in_raw = sorted(present)
        retracted -= present
    result.retracted_ids = len(retracted)
    result.found_in_sources = len(retracted & all_ids)

    def touches(batch: frozenset[str]) -> bool:
        return bool(batch & retracted)

    # "Rein" heisst weiter: der ganze Batch ist zurueckgenommen. Nach der
    # Angabe des Modells allein zu loeschen, verloere sauberes Wissen, wenn
    # es falsch zitiert hat - ohne rebuild wird nichts neu aufgebaut (Review
    # 2026-09-29). Die Angabe dient nur der Anzeige (result.cited_only).
    full = [e for e in entries if e.batches and e.other_sources == 0 and all(b <= retracted for b in e.batches)]
    result.cited_only = [e.label for e in entries if e not in full and e.cited and e.other_sources == 0
                         and all(c <= retracted for c in e.cited)]
    partial = [e for e in entries if e not in full and any(touches(b) for b in e.batches)]
    result.full = [e.label for e in full]
    result.partial = [e.label for e in partial]

    # Huelle ueber Nachrichten-IDs: jede Nachricht eines betroffenen Batches
    # wird neu eingestellt, also muss jeder Eintrag weg, der eine dieser
    # Nachrichten nennt - samt seiner uebrigen Batches (Verdichtung).
    affected_ids: set[str] = set(retracted)
    while True:
        grown = set(affected_ids)
        for e in entries:
            if any(b & affected_ids for b in e.batches):
                for b in e.batches:
                    grown |= b
        if grown == affected_ids:
            break
        affected_ids = grown
    closure = [e for e in entries if any(b & affected_ids for b in e.batches)]
    manual = [e for e in closure if e.other_sources > 0 and e not in full]
    result.manual = [e.label for e in manual] if rebuild else []

    doomed = [e for e in closure if e not in manual] if rebuild else full
    requeue: set[str] = set()
    if rebuild:
        # Nur entfernen, was sich danach wieder aufbauen laesst: jede
        # Quell-Nachricht eines entfernten Eintrags ist zurueckgenommen oder
        # wird neu eingestellt - und dafuer muss sie noch im Rohbestand liegen
        # und darf von keinem stehen bleibenden Eintrag genannt werden (sonst
        # Dublette). Alles andere bleibt stehen und wird gemeldet (Review
        # Runde 2: sauberes Wissen verschwand ohne Neuaufbau).
        in_raw = _raw_message_ids(project)
        # Eintraege mit zurueckgenommenem Inhalt kommen IMMER weg (Fremdinhalt
        # darf nicht bleiben; ihr Text steht im Protokoll). Nur unbeteiligte
        # Eintraege bleiben stehen, wenn sie sich nicht neu aufbauen lassen -
        # vorher hielt ein einziger solcher Eintrag die ganze Kette samt
        # verunreinigtem Eintrag zurueck (Review Runde 3).
        contaminated = [e for e in doomed if any(b & retracted for b in e.batches)]
        while True:
            kept = [e for e in entries if e not in doomed]
            kept_ids = {i for e in kept for b in e.batches for i in b}
            requeue = (affected_ids - retracted - kept_ids) & in_raw
            rebuildable = requeue | retracted
            still = [e for e in doomed if e in contaminated or all(b <= rebuildable for b in e.batches)]
            if len(still) == len(doomed):
                break
            manual = manual + [e for e in doomed if e not in still]
            doomed = still
        result.manual = [e.label for e in manual]
        result.not_rebuilt = [
            e.label for e in contaminated
            if not all(b <= (requeue | retracted) for b in e.batches)
        ]
    if any(e.file == "decisions.md" for e in doomed) and _duplicate_headings(texts.get("decisions.md", "")):
        raise RetractError(
            "decisions.md hat doppelte Ueberschriften ("
            + ", ".join(_duplicate_headings(texts["decisions.md"])[:3])
            + ") - erst von Hand eindeutig machen"
        )

    if not apply:
        result.removed = [e.label for e in doomed]
        result.requeued_ids = len(requeue)
        return result
    if not doomed and not requeue:
        return result

    from . import locking

    stamp = f"{datetime.now():%Y%m%d-%H%M%S-%f}"
    with locking.project_lock(project) as acquired:
        if not acquired:
            raise RetractError("Curator-Lauf fuer dieses Projekt aktiv (Lock) - spaeter erneut versuchen")
        from . import journal

        try:
            journal.ensure_none_pending(project)
        except journal.JournalConflict as exc:
            raise RetractError(str(exc)) from exc
        for name in texts:
            if (project / name).read_text(encoding="utf-8") != texts[name]:
                raise RetractError(f"{name} hat sich seit dem Lesen geaendert - bitte erneut ausfuehren")
        backups_dir = layout.polaris_dir(project) / "backups"
        storage.ensure_dir(backups_dir)
        retracted_dir = layout.polaris_dir(project) / "retracted"
        storage.ensure_dir(retracted_dir)

        lines = [
            f"# Zurückgenommene Einträge {project.name} ({stamp})",
            "",
            f"Modus: {'rebuild (betroffene Batches neu kuratieren)' if rebuild else 'nur reine Einträge'}.",
            f"Zurückgenommene Nachrichten ({len(retracted)}): {', '.join(sorted(retracted))}",
            "",
        ]
        for e in doomed:
            lines += [f"## {e.file} › {e.section}", "", e.text.strip(), ""]
        retracted_path = retracted_dir / f"{stamp}.md"
        storage.atomic_write_text(retracted_path, "\n".join(lines).rstrip() + "\n")
        result.retracted_file = str(retracted_path)

        # Erst den State (Nachrichten neu einstellen), dann die Dateien: bricht
        # es dazwischen ab, entstehen hoechstens Dubletten (die die Verdichtung
        # zusammenfuehrt) statt verlorener Eintraege.
        # Zurueckgenommene nie wieder kuratieren, auch nicht als neue Fassung.
        st_r = state_mod.load_state(project)
        if not retracted <= st_r.retracted_ids:
            st_r.retracted_ids |= retracted
            state_mod.save_state(project, st_r)
        if requeue:
            state_file = layout.state_path(project)
            if state_file.is_file():
                backup = backups_dir / f"curator-state.json.{stamp}-before-retract"
                storage.atomic_write_text(backup, state_file.read_text(encoding="utf-8"))
                result.backups.append(str(backup))
            st = state_mod.load_state(project)
            st.processed_message_ids -= requeue
            for day in st.daily_batches.values():
                before = len(day.processed_message_ids)
                day.processed_message_ids = [m for m in day.processed_message_ids if m not in requeue]
                if len(day.processed_message_ids) != before:
                    day.status = "open"
            state_mod.save_state(project, st)
        for name, kind in _FILES:
            mine = [e for e in doomed if e.file == name]
            if not mine:
                continue
            backup = backups_dir / f"{name}.{stamp}-before-retract"
            storage.atomic_write_text(backup, texts[name])
            result.backups.append(str(backup))
            storage.atomic_write_text(project / name, _remove_entries(kind, texts[name], mine))

    result.removed = [e.label for e in doomed]
    result.requeued_ids = len(requeue)
    result.written = True
    return result
