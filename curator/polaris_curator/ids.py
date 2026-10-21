"""Stabile, sichtbare Nummern fuer kuratierte Eintraege (seit 2026-09-29).

Konzept (erprobt u. a. in LLM-Gedaechtnis-Systemen): das Modell aendert einen
Eintrag ueber seine Nummer ("ersetze K12") statt ueber einen Textausschnitt,
den es wiederfinden muss. Textmarken trafen in Reviews mehrfach den falschen
Eintrag (z. B. "Konfidenz: hoch" -> beliebiger Eintrag).

Format, gut lesbar im Markdown (knowledge.md / open-loops.md als Zeile unter
dem Eintrag, decisions.md als eigenes Feld):

    - **[Fakt]** Die Hausverwaltung uebernimmt ab Oktober ...
      - Scope: ... · Evidenzgrad: ... · Konfidenz: ...
      - ID: K12 · Gültig seit: 2026-09-29
      <!-- polaris-source: ... -->

    ## 2026-09-29 – Titel
    **ID**
    D3

Praefixe: K = Wissen (auch im Archiv), D = Entscheidung, L = offener Punkt.
Nummern werden nie wiederverwendet: naechste = hoechste je gesehene + 1 -
aus Eintraegen, Archiv, Verweisen ("Zusammengeführt", "Ersetzt", "Abgelöst
durch") und dem Zaehler im Dateikopf (<!-- polaris-ids: K=.. -->).
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from . import merge

# Eine Nummer gilt nur als eigene Unterzeile "  - ID: K12 ..." (bzw. als
# **ID**-Feld in decisions.md) und nie innerhalb eines Code-Blocks - sonst
# galt z. B. "Kunden-ID: K7" im Fliesstext als Nummer (Review 2026-09-29).
ID_LINE_RE = re.compile(r"^\s+- ID: ([KL]\d+)(?=\s|$)")
ID_RE = ID_LINE_RE  # Rueckwaertskompatibel fuer Aufrufer
_DECISION_ID_RE = re.compile(r"^\*\*ID\*\*[ \t]*\n(D\d+)[ \t]*$", re.M)
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
# Fuer den Zaehler zaehlt jede Erwaehnung einer Nummer in einem
# Nummern-Zusammenhang - lieber eine Nummer zu viel ueberspringen als eine
# verschwundene (verdichtet, zurueckgenommen, von Hand geloescht) neu vergeben.
_REF_CTX_RE = re.compile(r"^\s+- (ID|Zusammengeführt|Abgelöst durch)[: ]|^<!-- polaris-ids:")
_REF_FIELDS = ("**ID**", "**Ersetzt durch**")
_REF_RE = re.compile(r"\b([KDL])(\d+)\b")
_COUNTER_RE = re.compile(r"^<!-- polaris-ids: (.*?) -->[ \t]*$", re.M)


def unfenced(lines: list[str]) -> list[tuple[int, str]]:
    """(Index, Zeile) aller Zeilen ausserhalb von Code-Bloecken."""
    out, fenced = [], False
    fences = [i for i, ln in enumerate(lines) if _FENCE_RE.match(ln)]
    for i, line in enumerate(lines):
        if _FENCE_RE.match(line):
            # Ein nie geschlossener Code-Block (Handnotiz) zaehlt nicht als
            # Block - sonst verschwaende der Rest der Datei darin.
            if fenced or any(j > i for j in fences[fences.index(i) + 1:]):
                fenced = not fenced
            continue
        if not fenced:
            out.append((i, line))
    return out


def _counter(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    m = _COUNTER_RE.search(text or "")
    if m:
        for part in m.group(1).split():
            k, _, v = part.partition("=")
            if k in "KDL" and v.isdigit():
                out[k] = int(v)
    return out


def with_counter(text: str, prefix: str, highest: int) -> str:
    """Hoechste je vergebene Nummer als Kommentarzeile im Dateikopf merken.
    Sie reist mit der Datei (und damit durchs Journal) und verhindert, dass
    eine verschwundene Nummer neu vergeben wird (Review 2026-09-29, HIGH)."""
    counts = _counter(text)
    if counts.get(prefix, 0) >= highest:
        return text
    counts[prefix] = highest
    line = "<!-- polaris-ids: " + " ".join(f"{k}={counts[k]}" for k in sorted(counts)) + " -->"
    if _COUNTER_RE.search(text):
        return _COUNTER_RE.sub(line, text, count=1)
    lines = text.splitlines()
    pos = 1 if lines and lines[0].startswith("# ") else 0
    lines.insert(pos, line)
    return "\n".join(lines) + ("\n" if text.endswith("\n") or not text else "")


def ids_in(text: str, prefix: str) -> set[str]:
    """Nummern, die als Eintragsnummer vergeben sind (streng)."""
    lines = (text or "").splitlines()
    found = {m.group(1) for _i, ln in unfenced(lines) if (m := ID_LINE_RE.match(ln)) and m.group(1)[0] == prefix}
    if prefix == "D":
        found |= set(_DECISION_ID_RE.findall("\n".join(ln for _i, ln in unfenced(lines))))
    return found


def highest_number(prefix: str, *texts: str) -> int:
    best = 0
    for t in texts:
        best = max(best, _counter(t).get(prefix, 0))
        lines = (t or "").splitlines()
        for i, ln in enumerate(lines):
            ctx = ln if _REF_CTX_RE.search(ln) else ""
            if not ctx and i > 0 and lines[i - 1].strip() in _REF_FIELDS:
                ctx = ln
            for p, n in _REF_RE.findall(ctx):
                if p == prefix:
                    best = max(best, int(n))
    return best


def next_number(prefix: str, *texts: str) -> int:
    return highest_number(prefix, *texts) + 1


_ALIAS_RE = re.compile(r"Zusammengeführt: ((?:[KL]\d+(?:, )?)+)")


def block_aliases(block: list[str]) -> set[str]:
    """Nummern, die in diesem Eintrag aufgegangen sind (Verdichtung)."""
    out: set[str] = set()
    for _i, line in unfenced(block):
        if ID_LINE_RE.match(line) and (m := _ALIAS_RE.search(line)):
            out.update(a.strip() for a in m.group(1).split(",") if a.strip())
    return out


def id_line_of(block: list[str]) -> str | None:
    for _i, line in unfenced(block):
        if ID_LINE_RE.match(line):
            return line
    return None


def block_id(block: list[str]) -> str | None:
    line = id_line_of(block)
    return ID_LINE_RE.match(line).group(1) if line else None


def id_line(entry_id: str, valid_from: str | None = None, extra: str | None = None) -> str:
    parts = [f"ID: {entry_id}"]
    if valid_from:
        parts.append(f"Gültig seit: {valid_from}")
    if extra:
        parts.append(extra)
    return "  - " + " · ".join(parts)


def entry_head(block: list[str]) -> tuple[list[str], list[str]]:
    """Teilt einen Block in den Eintrag selbst und angehaengte, von Hand
    geschriebene Zwischenueberschriften samt Inhalt ("### ..."). Nur der
    Eintrag wird ersetzt/verschoben (Review 2026-09-29)."""
    for i, line in unfenced(block):
        if i > 0 and line.startswith("#"):
            return block[:i], block[i:]
    return block, []


def _entries(lines: list[str]) -> list[tuple[int, int]]:
    """(Start, Ende exkl.) jedes "- "-Eintrags innerhalb von ##-Abschnitten;
    endet am naechsten Eintrag oder an einer Ueberschrift, Code-Bloecke
    beachtet."""
    spans, start, in_section = [], None, False
    for i, line in unfenced(lines):
        is_heading = line.startswith("#")
        if is_heading or line.startswith("- "):
            if start is not None:
                spans.append((start, i))
                start = None
        if line.startswith("## "):
            in_section = True
        elif not is_heading and line.startswith("- ") and in_section:
            start = i
    if start is not None:
        spans.append((start, len(lines)))
    return spans


def ensure_block_ids(text: str, prefix: str, start: int) -> tuple[str, int]:
    """Nummeriert alle "- "-Eintraege ohne ID (knowledge/open-loops/Archiv),
    indem es NUR je eine Zeile einfuegt - der Rest der Datei bleibt Byte fuer
    Byte, wie er ist (frueher: neu gerendert, dabei verschmolzen doppelte
    Ueberschriften, Review 2026-09-29). Liefert (Text, naechste freie Nummer)."""
    lines = text.splitlines()
    inserts: list[tuple[int, str]] = []
    n = start
    for s, e in _entries(lines):
        block = lines[s:e]
        if block_id(block) is not None:
            continue
        pos = None
        for i, ln in unfenced(block):
            if i > 0 and ln.strip().startswith("<!-- polaris-source"):
                pos = s + i
                break
        if pos is None:  # nach der letzten nicht-leeren Zeile des Eintrags
            pos = s + max(i for i, ln in enumerate(block) if ln.strip()) + 1
        inserts.append((pos, id_line(f"{prefix}{n}")))
        n += 1
    if not inserts:
        return text, n
    for pos, line in reversed(inserts):
        lines.insert(pos, line)
    return "\n".join(lines) + ("\n" if text.endswith("\n") else ""), n


def unique_headings(text: str) -> str:
    """Wiederholte ##-Ueberschriften bekommen " (2)", " (3)" ... - wie neue
    Entscheidungen mit gleichem Titel. Nur diese Zeilen aendern sich."""
    lines = text.splitlines()
    seen: set[str] = set()
    changed = False
    for i, ln in unfenced(lines):
        if not ln.startswith("## "):
            continue
        base = h = ln[3:].strip()
        n = 2
        while h in seen:
            h = f"{base} ({n})"
            n += 1
        if h != base:
            lines[i] = f"## {h}"
            changed = True
        seen.add(h)
    if not changed:
        return text
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def ensure_decision_ids(text: str, start: int) -> tuple[str, int]:
    """Je ##-Abschnitt ohne **ID** ein Feld direkt unter der Ueberschrift -
    auch doppelte Ueberschriften bekommen je eine eigene Nummer."""
    lines = text.splitlines()
    heads = [i for i, ln in unfenced(lines) if ln.startswith("## ")]
    inserts: list[int] = []
    for k, h in enumerate(heads):
        end = heads[k + 1] if k + 1 < len(heads) else len(lines)
        section = lines[h + 1:end]
        if any(ln.strip() == "**ID**" for _i, ln in unfenced(section)):
            continue
        inserts.append(h)
    n = start
    numbered: list[tuple[int, str]] = []
    for h in inserts:
        numbered.append((h, f"D{n}"))
        n += 1
    if not numbered:
        return text, n
    for h, did in reversed(numbered):
        lines[h + 1:h + 1] = ["**ID**", did, ""] if (h + 1 < len(lines) and lines[h + 1].strip()) else ["**ID**", did]
    return "\n".join(lines) + ("\n" if text.endswith("\n") else ""), n


def assign_missing(project: Path) -> dict[str, tuple[str, str]]:
    """Fuer die einmalige Nummerierung vorhandener Eintraege: liefert
    {Dateiname: (alter Text, neuer Text)} fuer Dateien, die sich aendern."""
    from .compact import ARCHIVE_NAME

    def read(name: str) -> str:
        p = project / name
        return p.read_text(encoding="utf-8") if p.is_file() else ""

    k_text, a_text = read("knowledge.md"), read(ARCHIVE_NAME)
    d_text, l_text = read("decisions.md"), read("open-loops.md")
    out: dict[str, tuple[str, str]] = {}
    k_first = next_number("K", k_text, a_text)
    new_k, k_next = ensure_block_ids(k_text, "K", k_first)
    new_a, k_next = ensure_block_ids(a_text, "K", k_next)
    d_first = next_number("D", d_text)
    new_d, d_next = ensure_decision_ids(unique_headings(d_text), d_first)
    l_first = next_number("L", l_text)
    new_l, l_next = ensure_block_ids(l_text, "L", l_first)
    if k_next > k_first:
        new_k = with_counter(new_k, "K", k_next - 1) if k_text else new_k
        if new_a != a_text:  # auch im Archiv merken (knowledge.md kann fehlen)
            new_a = with_counter(new_a, "K", k_next - 1)
    if d_next > d_first:
        new_d = with_counter(new_d, "D", d_next - 1)
    if l_next > l_first:
        new_l = with_counter(new_l, "L", l_next - 1)
    for name, old, new in (("knowledge.md", k_text, new_k), (ARCHIVE_NAME, a_text, new_a),
                           ("decisions.md", d_text, new_d), ("open-loops.md", l_text, new_l)):
        if old and new != old:
            out[name] = (old, new)
    return out


def today() -> str:
    return date.today().isoformat()
