"""Merged ein validiertes, strukturiertes Aenderungsobjekt gezielt in
knowledge.md / decisions.md / open-loops.md, statt die Dateien bei jedem
Lauf neu zu schreiben.

Provenance-Kommentare sind bewusst EINZEILIG (inhaltlich aequivalent zum
mehrzeiligen Beispiel in der Aufgabenstellung) - das macht das gezielte
Suchen/Verschieben einzelner Eintraege beim Merge robust und einfach,
ohne einen HTML-Kommentar-Parser zu benoetigen.

v0.2: Jeder knowledge_updates-Eintrag wird nicht mehr als einzelne
Markdown-Zeile, sondern als kleiner BLOCK gerendert (Klassifikations-Tag,
Meta-Zeile mit Scope/Evidenzgrad/Konfidenz, optionale
Implikations-/Kausalketten-Zeilen, Provenance). "replace" ersetzt daher
immer den GANZEN Block, in dem der replace_marker vorkommt, nie nur die
einzelne Zeile - sonst blieben Meta-/Provenance-Zeilen des alten Eintrags
verwaist im Dokument stehen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import layout, storage

HEADING2_RE = re.compile(r"^##\s+(.*)$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")

ENTRY_TYPE_LABELS = {
    "fact": "Fakt",
    "stakeholder_claim": "Stakeholder-Aussage",
    "hypothesis": "Hypothese",
    "implication": "Systemische Implikation",
}
SCOPE_LABELS = {
    "local": "lokal",
    "functional": "funktional",
    "cross_functional": "bereichsübergreifend",
    "company_wide": "unternehmensweit",
}
EVIDENCE_LABELS = {
    "single_source": "Einzelbeleg",
    "multiple_sources": "mehrere unabhängige Belege",
    "corroborated": "bestätigt",
}
CONFIDENCE_LABELS = {"low": "niedrig", "medium": "mittel", "high": "hoch"}
DECISION_SCOPE_LABELS = {
    "local": "lokal",
    "provisional": "vorläufig",
    "pilot": "Pilot/Test",
    "permanent": "dauerhaft",
    "company_wide": "unternehmensweiter Standard",
}
LOOP_TYPE_LABELS = {
    "data_need": "Datenbedarf",
    "leadership_question": "Führungsfrage",
    "decision_validation": "Wirksamkeitsprüfung",
    "process_gap": "Prozesslücke",
    "ownership_gap": "Verantwortungslücke",
    "economic_impact": "Wirtschaftliche Frage",
    "other": "Sonstiges",
}


_WS_RE = re.compile(r"\s+")


def clean(value: Any) -> str:
    """Text aus der Modellausgabe einzeilig und ohne HTML-Kommentarzeichen.
    Sonst konnte ein Zeilenumbruch mit "## " oder "- " einen neuen Abschnitt
    oder Eintrag erzeugen (die Herkunftsangabe hing dann am falschen Block),
    und ein "<!-- polaris-source ... -->" im Text wirkte wie eine echte
    Herkunft - retract haette ein fremdes Batch entfernt (Review 2026-09-29)."""
    text = "" if value is None else str(value)
    text = text.replace("<!--", "‹!--").replace("-->", "→")
    return _WS_RE.sub(" ", text).strip()


def _heading(value: Any) -> str:
    return clean(value).lstrip("#").strip() or "Allgemein"


def build_provenance(source_type: str, **fields: Any) -> str:
    parts = [f"type={source_type}"] + [f"{k}={v}" for k, v in fields.items() if v not in (None, "")]
    return "<!-- polaris-source: " + " ".join(parts) + " -->"


def parse_level2_sections(text: str) -> tuple[str, dict[str, str]]:
    lines = text.splitlines()
    preamble: list[str] = []
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        m = HEADING2_RE.match(line)
        if m:
            current = m.group(1).strip()
            sections.setdefault(current, [])
        elif current is None:
            preamble.append(line)
        else:
            sections[current].append(line)
    return "\n".join(preamble).rstrip("\n"), {k: "\n".join(v).strip("\n") for k, v in sections.items()}


def render_level2_sections(preamble: str, sections: dict[str, str]) -> str:
    parts = [preamble.rstrip("\n")]
    for heading, body in sections.items():
        body = body.strip("\n")
        if not body:
            continue
        parts.append(f"\n## {heading}\n{body}\n")
    return "\n".join(parts).strip("\n") + "\n"


def _split_into_blocks(body: str) -> list[list[str]]:
    """Teilt eine Sektion in "Eintrags-Bloecke": jede Zeile, die auf
    oberster Ebene mit '- ' beginnt, eroeffnet einen neuen Block; alle
    folgenden Zeilen (Meta-Unterpunkte, Provenance-Kommentar, Leerzeilen)
    gehoeren zum selben Block, bis der naechste Top-Level-'- '-Eintrag
    beginnt. Kompatibel mit dem alten, einzeiligen Format (dort ist ein
    Block einfach ['- Inhalt', '<!-- provenance -->'])."""
    blocks: list[list[str]] = []
    current: list[str] | None = None
    fenced = False
    lines = body.splitlines()
    fences = [i for i, ln in enumerate(lines) if FENCE_RE.match(ln)]
    for n, line in enumerate(lines):
        if FENCE_RE.match(line) and (fenced or fences[-1] > n):  # ungeschlossen: kein Block
            fenced = not fenced
        # "- " innerhalb eines Code-Blocks eroeffnet keinen Eintrag (sonst
        # landete die ID-Zeile in einem Scheinblock, Review 2026-09-29).
        if line.startswith("- ") and not fenced:
            if current is not None:
                blocks.append(current)
            current = [line]
        else:
            if current is None:
                current = [line]
            else:
                current.append(line)
    if current is not None:
        blocks.append(current)
    return blocks


def _join_blocks(blocks: list[list[str]]) -> str:
    return "\n".join(line for blk in blocks for line in blk)


def _render_knowledge_entry(item: dict, provenance: str) -> list[str]:
    entry_label = ENTRY_TYPE_LABELS.get(item["entry_type"], item["entry_type"])
    scope_label = SCOPE_LABELS.get(item["scope"], item["scope"])
    evidence_label = EVIDENCE_LABELS.get(item["evidence_strength"], item["evidence_strength"])
    confidence_label = CONFIDENCE_LABELS.get(item["confidence"], item["confidence"])

    lines = [f"- **[{entry_label}]** {clean(item['content'])}"]
    lines.append(
        f"  - Scope: {scope_label} · Evidenzgrad: {evidence_label} · Konfidenz: {confidence_label}"
    )
    if item.get("economic_relevance"):
        lines.append(f"  - Wirtschaftliche Relevanz: {clean(item['economic_relevance'])}")
    if item.get("operating_model_relevance"):
        lines.append(f"  - Operating-Model-Relevanz: {clean(item['operating_model_relevance'])}")
    if item.get("key_person_dependency"):
        lines.append(f"  - Key-Person-Abhängigkeit: {clean(item['key_person_dependency'])}")
    if item.get("causal_chain"):
        lines.append("  - Wirkungskette (Hypothese): " + " → ".join(clean(c) for c in item["causal_chain"]))
    if item.get("review_note"):
        lines.append(f"  - Prüfhinweis: {clean(item['review_note'])}")
    lines.append(f"  {provenance}")
    return lines


@dataclass
class MergeResult:
    knowledge_changed: bool = False
    decisions_changed: bool = False
    open_loops_changed: bool = False
    warnings: list[str] = field(default_factory=list)
    files: dict = field(default_factory=dict)


def _prov(provenance, item: dict | None = None) -> str:
    """Herkunftsangabe fuer einen Eintrag. provenance ist entweder fester Text
    (Dokument) oder eine Funktion, die aus den source_message_ids des
    Eintrags die Angabe baut (Chat; Herkunft je Aussage seit 2026-09-29)."""
    if callable(provenance):
        return provenance(list((item or {}).get("source_message_ids") or []))
    return provenance


def _find_block(sections: dict[str, str], entry_id: str, archive_text: str = "") -> tuple[str, int] | None:
    from . import ids

    for heading, body in sections.items():
        for i, blk in enumerate(_split_into_blocks(body)):
            if blk and blk[0].startswith("- ") and (
                ids.block_id(blk) == entry_id or entry_id in ids.block_aliases(blk)
            ):
                return heading, i
    # Schon abgeloest: der Nachfolger traegt "Ersetzt: <Nummer>" - der Kette
    # folgen, statt eine Dublette anzuhaengen (das Modell kennt evtl. eine alte
    # Nummer). Nachfolger im Archiv werden ueber dessen Vermerk gefunden.
    successors: dict[str, str] = {}
    for body in list(sections.values()) + list(parse_level2_sections(archive_text)[1].values()):
        for blk in _split_into_blocks(body):
            line = ids.id_line_of(blk) if blk and blk[0].startswith("- ") else None
            m = re.search(r"· Ersetzt: ([KL]\d+)(?=\s|$)", line or "")
            if m:
                successors[m.group(1)] = ids.block_id(blk)
    seen = {entry_id}
    current = entry_id
    while current in successors and successors[current] not in seen:
        current = successors[current]
        seen.add(current)
        for heading, body in sections.items():
            for i, blk in enumerate(_split_into_blocks(body)):
                if blk and blk[0].startswith("- ") and (
                        ids.block_id(blk) == current or current in ids.block_aliases(blk)):
                    return heading, i
    return None


def _archive_superseded(archive_text: str, section: str, block: list[str], new_id: str, day: str) -> str:
    """Abgeloesten Eintrag ins Wissensarchiv (nicht loeschen) mit Vermerk."""
    preamble, sections = parse_level2_sections(archive_text)
    if not preamble.strip():
        preamble = (
            "# Wissensarchiv\n\nAbgelöste und als überholt markierte Einträge aus `knowledge.md` "
            "(nicht gelöscht, nur verschoben). Automatisch gepflegt von Polaris Curator."
        )
    heading = f"Abgelöst {day}"
    lines = [f"### Abschnitt „{section}“", *block, f"  - Abgelöst durch {new_id} am {day}"]
    body = sections.get(heading, "")
    sections[heading] = (body.rstrip("\n") + "\n" if body else "") + "\n".join(lines)
    return render_level2_sections(preamble, sections)


def apply_knowledge_updates(
    existing_text: str, updates: list[dict], provenance, warnings: list[str],
    archive_text: str = "", day: str | None = None,
) -> tuple[str, str]:
    """Liefert (knowledge.md, knowledge-archiv.md). Ersetzen geschieht ueber
    die Nummer des bestehenden Eintrags (replaces_id); der abgeloeste Eintrag
    wandert mit "Abgelöst durch" ins Archiv."""
    from . import ids

    day = day or ids.today()
    preamble, sections = parse_level2_sections(existing_text)
    if not preamble.strip():
        preamble = "# Projektwissen"
    first_k = next_k = ids.next_number("K", existing_text, archive_text)

    redirect: dict[str, str] = {}  # zweimal dieselbe Nummer ersetzt: auf die neue umleiten
    for item in updates:
        section = _heading(item["section"])
        entry_id = f"K{next_k}"
        next_k += 1
        prov = _prov(provenance, item)
        new_block = _render_knowledge_entry(item, prov)
        replaces = clean(item.get("replaces_id") or "").upper()
        while replaces in redirect:
            replaces = redirect[replaces]
        carried: list[str] = []
        extra = None

        if item["mode"] == "replace" and replaces:
            where = _find_block(sections, replaces, archive_text)
            if where is None:
                warnings.append(f"knowledge_updates: {replaces} nicht gefunden - als neuer Eintrag angehaengt")
            else:
                old_section, idx = where
                old_blocks = _split_into_blocks(sections[old_section])
                # Von Hand angehaengte "### ..."-Unterabschnitte bleiben stehen.
                old_block, tail = ids.entry_head(old_blocks.pop(idx))
                if tail:
                    old_blocks.insert(idx, tail)
                sections[old_section] = _join_blocks(old_blocks)
                archive_text = _archive_superseded(archive_text, old_section, old_block, entry_id, day)
                # Herkunft des abgeloesten Eintrags mitnehmen: sein Inhalt kann
                # eingeflossen sein, und retract muss auch ueber ihn finden.
                carried = [ln.strip() for ln in old_block
                           if ln.strip().startswith("<!-- polaris-source") and ln.strip() != prov.strip()]
                actual = ids.block_id(old_block) or replaces  # ggf. der Nachfolger
                extra = f"Ersetzt: {actual}"
                redirect[replaces] = redirect[actual] = entry_id
        elif item["mode"] == "replace":
            warnings.append("knowledge_updates: replace ohne replaces_id - als neuer Eintrag angehaengt")

        new_block = new_block[:-1] + [ids.id_line(entry_id, day, extra), new_block[-1]] + [f"  {c}" for c in carried]
        blocks = _split_into_blocks(sections.get(section, ""))
        blocks.append(new_block)
        sections[section] = _join_blocks(blocks)

    if next_k > first_k:
        preamble = ids.with_counter(preamble, "K", next_k - 1)
    return render_level2_sections(preamble, sections), archive_text


def _replace_field_line(body: str, field_name: str, new_value: str) -> str:
    lines = body.splitlines()
    marker = f"**{field_name}**"
    for i, line in enumerate(lines):
        if line.strip() == marker:
            if i + 1 < len(lines):
                lines[i + 1] = new_value
            else:
                lines.append(new_value)
            return "\n".join(lines)
    return body.rstrip("\n") + f"\n\n**{field_name}**\n{new_value}\n"


def apply_decisions(
    existing_text: str, adds: list[dict], updates: list[dict], provenance, warnings: list[str],
    day: str | None = None,
) -> str:
    from . import ids

    day = day or ids.today()
    # Doppelte Ueberschriften eindeutig machen, sonst legt das Parsen zwei
    # Entscheidungen zusammen (Review 2026-09-29).
    existing_text = ids.unique_headings(existing_text)
    preamble, sections = parse_level2_sections(existing_text)
    if not preamble.strip():
        preamble = "# Entscheidungen"
    first_d = next_d = ids.next_number("D", existing_text)

    for item in adds:
        date = clean(item["date"])
        title = clean(item["title"])
        # Das Modell liefert den Titel gelegentlich bereits mit fuehrendem
        # Datum - ohne diese Bereinigung entstuende sonst z.B.
        # "## 2026-09-14 – 2026-09-14 – ..." (in echten Laeufen beobachtet).
        if title.startswith(date):
            title = title[len(date):].lstrip(" –-").strip() or title
        heading = f"{date} – {title}"
        original_heading = heading
        n = 2
        while heading in sections:
            heading = f"{original_heading} ({n})"
            n += 1
        scope_label = DECISION_SCOPE_LABELS.get(item["decision_scope"], item["decision_scope"])
        body = (
            f"**ID**\nD{next_d}\n\n"
            f"**Gültig seit**\n{day}\n\n"
            f"**Entscheidung**\n{clean(item['decision'])}\n\n"
            f"**Begründung**\n{clean(item['rationale'])}\n\n"
            f"**Geltungsbereich**\n{scope_label}\n\n"
            "**Status**\nAktiv\n\n"
            "**Wirksamkeit geprüft**\nNein (noch nicht überprüft)\n\n"
            + (f"**Prüfhinweis**\n{clean(item['review_note'])}\n\n" if item.get("review_note") else "")
            + f"**Quelle**\n{_prov(provenance, item)}\n"
        )
        next_d += 1
        sections[heading] = body

    for item in updates:
        wanted = clean(item.get("decision_id") or "").upper()
        heading = None
        if wanted:
            heading = next((h for h, b in sections.items() if wanted in ids.ids_in(b, "D")), None)
            if heading is None:
                warnings.append(f"decisions_update: {wanted} nicht gefunden")
                continue
        else:
            warnings.append("decisions_update: ohne decision_id - uebergangen")
            continue
        body = sections[heading]
        if body.count("**ID**") > 1:
            # Doppelte Ueberschrift: parse_level2_sections hat zwei
            # Entscheidungen zusammengelegt - lieber nichts aendern als die
            # falsche (Review 2026-09-29).
            warnings.append(f"decisions_update: {wanted} steht unter einer doppelten Ueberschrift - uebergangen")
            continue
        if item.get("new_status"):
            body = _replace_field_line(body, "Status", clean(item["new_status"]))
        if item.get("replaced_by"):
            body = _replace_field_line(body, "Ersetzt durch", f"{clean(item['replaced_by'])} am {day}")
        if item.get("validated") is not None:
            value = "Ja" if item["validated"] else "Nein (noch nicht bestätigt)"
            if item.get("validation_note"):
                value += f" — {clean(item['validation_note'])}"
            body = _replace_field_line(body, "Wirksamkeit geprüft", value)
        if item.get("note"):
            body = body.rstrip("\n") + f"\n\n**Notiz**\n{clean(item['note'])}\n"
        prov = _prov(provenance, item)
        # Auch Aktualisierungen tragen ihre Herkunft (sonst nicht ruecknehmbar).
        if prov.strip() not in body:
            body = body.rstrip("\n") + f"\n\n**Quelle (Aktualisierung {day})**\n{prov}\n"
        sections[heading] = body

    if next_d > first_d:
        preamble = ids.with_counter(preamble, "D", next_d - 1)
    return render_level2_sections(preamble, sections)


def apply_open_loops(
    existing_text: str, adds: list[dict], closes: list[dict], provenance, warnings: list[str],
    day: str | None = None,
) -> str:
    from . import ids

    day = day or ids.today()
    preamble, sections = parse_level2_sections(existing_text)
    if not preamble.strip():
        preamble = "# Offene Punkte"
    first_l = next_l = ids.next_number("L", existing_text)

    offen = _split_into_blocks(sections.get("Offen", ""))
    erledigt = _split_into_blocks(sections.get("Erledigt", ""))

    for item in adds:
        label = LOOP_TYPE_LABELS.get(item.get("loop_type"), "Sonstiges")
        offen.append([
            f"- **[{label}]** {clean(item['text'])}",
            f"  - ID: L{next_l} · offen seit {day}",
            *([f"  - Prüfhinweis: {clean(item['review_note'])}"] if item.get("review_note") else []),
            _prov(provenance, item),
        ])
        next_l += 1

    for item in closes:
        wanted = clean(item.get("loop_id") or "").upper()
        idx = next((i for i, blk in enumerate(offen)
                    if blk and blk[0].startswith("- ") and ids.block_id(blk) == wanted), None) if wanted else None
        if idx is None:
            warnings.append(f"open_loops_close: {wanted or '(ohne loop_id)'} nicht gefunden")
            continue
        moved = list(offen.pop(idx))
        note = f": {clean(item['resolution_note'])}" if item.get("resolution_note") else ""
        moved.append(f"  - erledigt am {day}{note}")
        prov = _prov(provenance, item)
        if prov.strip() not in "\n".join(moved):
            moved.append(f"  {prov}")
        erledigt.append(moved)

    ordered = {
        "Offen": "\n".join(l for l in _join_blocks(offen).splitlines() if l.strip()),
        "Erledigt": "\n".join(l for l in _join_blocks(erledigt).splitlines() if l.strip()),
    }
    for k, v in sections.items():
        if k not in ordered:
            ordered[k] = v

    if next_l > first_l:
        preamble = ids.with_counter(preamble, "L", next_l - 1)
    return render_level2_sections(preamble, ordered)


def apply_change_object(project: Path, change: dict, provenance, writer=None) -> MergeResult:
    """Liest die drei kuratierten Dateien, wendet alle Aenderungen NUR im
    Speicher an und schreibt erst danach atomar. Schlaegt irgendetwas
    fehl, bleiben alle drei Dateien unveraendert."""
    warnings: list[str] = []

    knowledge_file = layout.knowledge_path(project)
    decisions_file = layout.decisions_path(project)
    open_loops_file = layout.open_loops_path(project)

    knowledge_text = knowledge_file.read_text(encoding="utf-8") if knowledge_file.is_file() else "# Projektwissen\n"
    decisions_text = decisions_file.read_text(encoding="utf-8") if decisions_file.is_file() else "# Entscheidungen\n"
    open_loops_text = (
        open_loops_file.read_text(encoding="utf-8") if open_loops_file.is_file() else "# Offene Punkte\n"
    )

    # Ungeklaerte Widersprueche werden als eigene Open Loops erfasst
    # (Abschnitt 24), nicht in knowledge.md geschrieben.
    open_loops_adds = list(change["open_loops_add"])
    for c in change["conflicts"]:
        text = f"Widerspruch: {clean(c['topic'])} — aktuell: {clean(c['current_stand'])} / vorher: {clean(c['previous_stand'])}"
        if c.get("note"):
            text += f" ({clean(c['note'])})"
        open_loops_adds.append({"text": text, "loop_type": "decision_validation"})

    from .compact import ARCHIVE_NAME

    archive_file = project / ARCHIVE_NAME
    archive_text = archive_file.read_text(encoding="utf-8") if archive_file.is_file() else ""
    new_knowledge, new_archive = knowledge_text, archive_text
    if change["knowledge_updates"]:
        new_knowledge, new_archive = apply_knowledge_updates(
            knowledge_text, change["knowledge_updates"], provenance, warnings, archive_text
        )

    new_decisions = decisions_text
    if change["decisions_add"] or change["decisions_update"]:
        new_decisions = apply_decisions(
            decisions_text, change["decisions_add"], change["decisions_update"], provenance, warnings
        )

    new_open_loops = open_loops_text
    if open_loops_adds or change["open_loops_close"]:
        new_open_loops = apply_open_loops(
            open_loops_text, open_loops_adds, change["open_loops_close"], provenance, warnings
        )

    result = MergeResult(warnings=warnings)

    # Alle Aenderungen zusammen uebergeben: writer (Pipeline) legt sie erst im
    # Journal ab und schreibt dann (journal.py); ohne writer direkt.
    files: dict[str, tuple[str | None, str]] = {}
    if new_knowledge != knowledge_text:
        files[knowledge_file.name] = (knowledge_file.read_text(encoding="utf-8") if knowledge_file.is_file() else None, new_knowledge)
        result.knowledge_changed = True
    if new_decisions != decisions_text:
        files[decisions_file.name] = (decisions_file.read_text(encoding="utf-8") if decisions_file.is_file() else None, new_decisions)
        result.decisions_changed = True
    if new_open_loops != open_loops_text:
        files[open_loops_file.name] = (open_loops_file.read_text(encoding="utf-8") if open_loops_file.is_file() else None, new_open_loops)
        result.open_loops_changed = True
    if new_archive != archive_text:
        files[ARCHIVE_NAME] = (archive_text if archive_file.is_file() else None, new_archive)
        result.knowledge_changed = True
    result.files = files
    if writer is not None:
        writer(files)
    else:
        for name, (_old, new) in files.items():
            storage.atomic_write_text(project / name, new)
    return result
