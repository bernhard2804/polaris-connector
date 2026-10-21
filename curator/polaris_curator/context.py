"""Kuratierten Kontext fuer einen Modellaufruf zusammenstellen (seit 2026-09-30).

Vorher gingen knowledge.md, decisions.md und open-loops.md vollstaendig mit
(je bis max_curated_context_chars, dann von hinten gekuerzt). Bei einem Kundenprojekt
waren das ~280k Zeichen pro Aufruf fuer oft ~10k neue Inhalte, und ab der
Grenze sah das Modell die aeltesten Eintraege gar nicht mehr (Dubletten,
nicht ersetzbar).

Zwei Stufen, beide nur fuer den Prompt - die Dateien bleiben unveraendert:

1. Herkunftskommentare (<!-- polaris-source ... -->) und die Zaehlerzeile
   entfallen. Sie sind 36-70 % des Textes, und das Modell braucht sie nicht
   (die Herkunft neuer Eintraege kommt aus dem Batch).
2. Ist eine Datei danach noch groesser als full_chars, gilt das erprobte
   Muster "Verzeichnis + Volltext des Relevanten": jeder Eintrag steht mit
   Nummer und erster Zeile im Verzeichnis; im Volltext stehen die neuesten
   und die zum Quellinhalt passenden (Wortueberschneidung, gewichtet nach
   Seltenheit, reines Python). Eintraege nur aus dem Verzeichnis darf das
   Modell nicht ersetzen (pipeline: wird angehaengt) - lieber eine Dublette,
   die die Verdichtung aufraeumt, als eine Abloesung, deren Details das Modell
   nie gesehen hat.

Seit 2026-10-15 ist das Verzeichnis gedeckelt (index_cap, Anteil von
full_chars): Es wuchs mit jedem Eintrag um ~220 Zeichen und frass bei einem
Kundenprojekt mit 184 Eintraegen schon die Haelfte des Budgets. Jetzt stehen
dort nur die passendsten der uebrigen Eintraege (gleiche Bewertung wie fuer
den Volltext, bei Gleichstand die neueren), je Abschnitt ergaenzt um
"+N weitere ... nicht gezeigt". Nicht gezeigte Eintraege sind wie
Verzeichnis-Eintraege nie ersetzbar. Verzeichniszeilen sind auf 100 Zeichen
gekuerzt (vorher 200). Damit bleibt der Kontext je Aufruf begrenzt, egal wie
gross die Datei wird.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from . import ids, merge

PROVENANCE_LINE_RE = re.compile(r"^\s*<!-- polaris-(?:source|ids):.*?-->\s*$")
_WORD_RE = re.compile(r"[0-9A-Za-zÄÖÜäöüß]+")
INDEX_MARK = "(nur Verzeichnis)"
INDEX_LINE_CHARS = 100  # erste Zeile eines Eintrags im Verzeichnis (vorher 200)
INDEX_CAP = 0.25  # hoechstens dieser Anteil von full_chars fuer Verzeichniszeilen
# Status-Eintraege sollen abgeloest statt angehaengt werden (prompts.py,
# Abschnitt 10) - das geht nur, wenn das Modell sie im Volltext sieht.
STATUS_SECTIONS = frozenset({"Aktueller Status"})
STATUS_BOOST = 2.0
# Haeufige Woerter ohne Unterscheidungskraft (deutsch/englisch, klein).
_STOP = frozenset("""
aber alle allem allen aller alles also auch auf aus bei beim bereits bis bisher dann das dass dem den denen der
des dessen die dies diese diesem diesen dieser dieses doch dort durch eine einem einen einer eines erst etwa
fuer für gegen hat hatte hier ihre ihrem ihren ihrer immer ist jede jedem jeden jeder jetzt kann kein keine
mehr mit muss nach nicht noch nur oder ohne sehr seit sich sie sind soll sollen sowie ueber über und uns unter
vom von vor war waren was weil wenn werden wie wird wurde wurden zum zur zwischen the and for with that this
from are was were have has not you your will would can could should also into than then them they their there
fakt annahme hypothese scope evidenzgrad konfidenz lokal einzelbeleg mittel hoch niedrig gültig seit
""".split())


def strip_provenance(text: str) -> str:
    return "\n".join(ln for ln in (text or "").splitlines() if not PROVENANCE_LINE_RE.match(ln))


def _tokens(text: str) -> set[str]:
    """Merkmale fuer den Abgleich, ohne Wortstamm-Bibliothek: Bindestrich-
    Teile einzeln ("PZE-Einführung" -> pze, einfuehrung), Wortanfang (6
    Zeichen, faengt Beugung ab: Bewertungsdifferenz/-en) und bei langen
    Woertern 5-Zeichen-Stuecke (faengt Komposita: Inventurtest ~
    Testinventur, Stücklistenlogik ~ Stückliste). Seltene Merkmale zaehlen
    mehr (idf in select) - Allerweltsstuecke fallen dort weg (Review
    2026-09-30)."""
    out: set[str] = set()
    for t in _WORD_RE.findall(text or ""):
        w = t.lower()
        if len(w) < 3 or w in _STOP:
            continue
        out.add(w)  # exaktes Wort (selten, zaehlt viel)
        out.add(w[:6])
        if len(w) >= 8 and not w.isdigit():
            out.update("#" + w[i:i + 5] for i in range(len(w) - 4))
    return out


@dataclass
class _Entry:
    section: str
    lines: list[str]
    entry_id: str | None
    order: int

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass
class Context:
    knowledge: str
    decisions: str
    open_loops: str
    index_only: set[str] = field(default_factory=set)  # Nummern ohne Volltext im Prompt
    notes: list[str] = field(default_factory=list)
    hidden: int = 0  # Eintraege weder im Volltext noch im Verzeichnis


def _entries(text: str) -> tuple[str, list[tuple[str, list]], list[_Entry]]:
    """(Vorspann, [(Abschnitt, Bloecke)], Eintraege) - Bloecke sind Eintraege
    oder freie Zeilen (bleiben im Volltext)."""
    preamble, sections = merge.parse_level2_sections(text)
    out_sections, entries, n = [], [], 0
    for heading, body in sections.items():
        blocks = []
        for blk in merge._split_into_blocks(body):
            if blk and blk[0].startswith("- "):
                e = _Entry(heading, blk, ids.block_id(ids.entry_head(blk)[0]), n)
                n += 1
                entries.append(e)
                blocks.append(e)
            else:
                blocks.append(blk)
        out_sections.append((heading, blocks))
    return preamble, out_sections, entries


def _decision_entries(text: str) -> tuple[str, list[tuple[str, list]], list[_Entry]]:
    """decisions.md: ein ##-Abschnitt je Entscheidung."""
    preamble, sections = merge.parse_level2_sections(text)
    out_sections, entries = [], []
    for n, (heading, body) in enumerate(sections.items()):
        found = ids.ids_in(body, "D")
        first = min(found, key=_number) if found else None
        e = _Entry(heading, [f"## {heading}", *body.splitlines()], first, n)
        entries.append(e)
        out_sections.append((heading, [e]))
    return preamble, out_sections, entries


def _number(entry_id: str | None) -> int:
    try:
        return int((entry_id or "")[1:])
    except ValueError:
        return -1


def _scores(entries: list[_Entry], query: str) -> list[float]:
    """Passung jedes Eintrags zum Quellinhalt: gemeinsame Merkmale, gewichtet
    nach Seltenheit (idf), normiert auf die Eintragslaenge."""
    docs = [_tokens(e.text) for e in entries]
    df: dict[str, int] = {}
    for d in docs:
        for t in d:
            df[t] = df.get(t, 0) + 1
    n = len(entries)
    q = _tokens(query)
    # Merkmale in mehr als der Haelfte der Eintraege (z. B. "2026") tragen
    # nichts zur Unterscheidung bei.
    q = {t for t in q if t in df and df[t] <= n / 2}
    scores = []
    for d in docs:
        common = q & d
        s = sum(math.log((n + 1) / df[t]) for t in common)
        scores.append(s / math.sqrt(max(len(d), 1)) * 10)
    return [sc * STATUS_BOOST if sc > 0 and e.section in STATUS_SECTIONS else sc
            for sc, e in zip(scores, entries)]


def _by_recency(entries: list[_Entry]) -> list[int]:
    return sorted(range(len(entries)), key=lambda i: (_number(entries[i].entry_id), entries[i].order), reverse=True)


def select(entries: list[_Entry], query: str, budget: int, newest: int,
           scores: list[float] | None = None) -> set[int]:
    """Positionen der Eintraege mit Volltext: die `newest` neuesten (hoechste
    Nummer bzw. zuletzt in der Datei), dann nach Passung zum Quellinhalt,
    bis das Zeichenbudget erschoepft ist."""
    if not entries:
        return set()
    n = len(entries)
    if scores is None:
        scores = _scores(entries, query)
    chosen: set[int] = set()
    used = 0
    by_recency = _by_recency(entries)
    for i in by_recency[:newest]:
        chosen.add(i)
        used += len(entries[i].text)
    for i in sorted(range(n), key=lambda i: -scores[i]):
        if scores[i] <= 0:
            break
        if i in chosen:
            continue
        cost = len(entries[i].text)
        if used + cost > budget:
            continue
        chosen.add(i)
        used += cost
    return chosen


def _index_line(e: _Entry, is_decision: bool) -> str:
    if is_decision:
        return f"- {e.entry_id or '?'} {INDEX_MARK}: {e.section[:INDEX_LINE_CHARS]}"
    first = e.lines[0][2:] if e.lines and e.lines[0].startswith("- ") else (e.lines[0] if e.lines else "")
    if len(first) > INDEX_LINE_CHARS:
        first = first[:INDEX_LINE_CHARS].rsplit(" ", 1)[0] + " …"
    return f"- {e.entry_id or '?'} {INDEX_MARK}: {first}"


def _hidden_line(count: int, is_decision: bool) -> str:
    what = "Entscheidungen" if is_decision else "Einträge in diesem Abschnitt"
    return f"- (+{count} weitere {what} nicht gezeigt)"


def _render(preamble: str, sections: list[tuple[str, list]], chosen_ids: set[int], is_decision: bool,
            listed: set[int] | None = None) -> str:
    """listed: Positionen mit Verzeichniszeile; None = alle uebrigen. Was
    weder gewaehlt noch gelistet ist, wird je Abschnitt nur gezaehlt."""
    out = [preamble.rstrip("\n")] if preamble.strip() else []
    index_block: list[str] = []
    hidden_dec = 0
    for heading, blocks in sections:
        body: list[str] = []
        hidden = 0
        for b in blocks:
            if isinstance(b, _Entry):
                if b.order in chosen_ids:
                    if is_decision:
                        out += ["", *b.lines]
                        continue
                    body += b.lines
                elif listed is not None and b.order not in listed:
                    hidden += 1
                elif is_decision:
                    index_block.append(_index_line(b, True))
                else:
                    body.append(_index_line(b, False))
                    body += ids.entry_head(b.lines)[1]  # von Hand angehaengte ###-Teile bleiben sichtbar
            else:
                body += b
        if is_decision:
            hidden_dec += hidden
        else:
            if hidden:
                body.append(_hidden_line(hidden, False))
            out += ["", f"## {heading}", *body]
    if is_decision and (index_block or hidden_dec):
        out += ["", "## Weitere Entscheidungen " + INDEX_MARK, *index_block]
        if hidden_dec:
            out.append(_hidden_line(hidden_dec, True))
    return "\n".join(out).strip("\n") + "\n"


_ALIAS_REF_RE = re.compile(r"(?:Zusammengeführt|Ersetzt): ((?:[KL]\d+(?:, )?)+)")


def _fixed_chars(preamble: str, sections: list[tuple[str, list]]) -> int:
    """Vorspann und freier Text (keine Eintraege) - gehen immer mit."""
    return len(preamble) + sum(
        len("\n".join(b)) + 1 for _h, blocks in sections for b in blocks if not isinstance(b, _Entry))


def _index_reserve(sections: list[tuple[str, list]], entries: list[_Entry], full_chars: int,
                   is_decision: bool, index_cap: float) -> int:
    """Platz fuer das Verzeichnis: alle Zeilen, hoechstens index_cap *
    full_chars (plus je Abschnitt eine "+N weitere"-Zeile)."""
    total = sum(len(_index_line(e, is_decision)) + 1 for e in entries)
    if index_cap <= 0:
        return total
    return min(total, int(full_chars * index_cap) + len(sections) * (len(_hidden_line(999, is_decision)) + 1))


def _shrink(text: str, query: str, full_chars: int, newest: int, is_decision: bool,
            index_cap: float = INDEX_CAP) -> tuple[str, set[str], int]:
    parse = _decision_entries if is_decision else _entries
    preamble, sections, entries = parse(text)
    # Das Budget gilt fuer das ganze Ergebnis: Verzeichniszeilen und freier
    # Text gehen ab, sonst lag das Ergebnis ueber der Grenze und die
    # Kuerzung von hinten warf wieder Eintraege weg (Review 2026-09-30, M1).
    fixed = _fixed_chars(preamble, sections) + _index_reserve(sections, entries, full_chars, is_decision, index_cap)
    scores = _scores(entries, query)
    chosen = select(entries, query, max(0, full_chars - fixed), newest, scores)
    listed: set[int] | None = None
    if index_cap > 0:
        # Verzeichnis: die passendsten uebrigen Eintraege, bei Gleichstand
        # die neueren, bis zum Deckel.
        rank = {i: r for r, i in enumerate(_by_recency(entries))}
        rest = sorted((i for i in range(len(entries)) if i not in chosen), key=lambda i: (-scores[i], rank[i]))
        listed, used, cap = set(), 0, int(full_chars * index_cap)
        for i in rest:
            cost = len(_index_line(entries[i], is_decision)) + 1
            if used + cost > cap:
                continue
            listed.add(i)
            used += cost
    hidden = 0 if listed is None else len(entries) - len(chosen) - len(listed)
    idx_only: set[str] = set()
    for e in entries:
        if e.order in chosen or not e.entry_id:
            continue
        idx_only.add(e.entry_id)
        # Auch Nummern, die in diesem Eintrag aufgegangen sind oder von ihm
        # abgeloest wurden - merge._find_block loest sie hierher auf (L1).
        line = ids.id_line_of(e.lines) or ""
        for m in _ALIAS_REF_RE.finditer(line):
            idx_only.update(x.strip() for x in m.group(1).split(",") if x.strip())
    # Was irgendwo im Volltext steht (eigene Nummer oder Alias), bleibt ersetzbar.
    for e in entries:
        if e.order in chosen and e.entry_id:
            idx_only.discard(e.entry_id)
            for m in _ALIAS_REF_RE.finditer(ids.id_line_of(e.lines) or ""):
                idx_only.difference_update(x.strip() for x in m.group(1).split(","))
    return _render(preamble, sections, chosen, is_decision, listed), idx_only, hidden


def build(knowledge: str, decisions: str, open_loops: str, query: str,
          full_chars: int = 80000, newest: int = 12, max_chars: int | None = None,
          index_cap: float = INDEX_CAP) -> Context:
    """full_chars: bis zu dieser Groesse (ohne Herkunft) geht eine Datei
    vollstaendig mit; darueber Verzeichnis + Volltext bis full_chars.
    full_chars <= 0: keine Auswahl (nur Herkunft entfernen).
    index_cap: Anteil von full_chars fuer das Verzeichnis; 0 = alle Eintraege
    im Verzeichnis (Verhalten bis 2026-10-15)."""
    k, d, lp = strip_provenance(knowledge), strip_provenance(decisions), strip_provenance(open_loops)
    ctx = Context(k, d, lp)
    if full_chars <= 0:
        return ctx
    if max_chars:
        full_chars = min(full_chars, int(max_chars * 0.9))  # Luft zur harten Grenze
    for attr, is_dec in (("knowledge", False), ("decisions", True), ("open_loops", False)):
        text = getattr(ctx, attr)
        if len(text) <= full_chars:
            continue
        new, idx_only, hidden = _shrink(text, query, full_chars, newest, is_dec, index_cap)
        setattr(ctx, attr, new)
        ctx.index_only |= idx_only
        ctx.hidden += hidden
        ctx.notes.append(f"{attr}: {len(idx_only)} Eintraege nur im Verzeichnis, davon {hidden} nicht gezeigt "
                         f"({len(text)} -> {len(new)} Zeichen)")
    return ctx


def index_share(text: str, full_chars: int, is_decision: bool = False, index_cap: float = INDEX_CAP) -> float:
    """Anteil des Volltext-Budgets, den Verzeichnis und freier Text schon
    belegen (0..). Ab 0.5 bleibt fuer Volltext nur noch die Haelfte - Zeit
    zum Aufraeumen (Frühwarnung im Health Check, seit 2026-09-30). Mit
    Deckel (seit 2026-10-15) waechst der Verzeichnisanteil nicht mehr mit;
    dann warnt das nur noch bei viel freiem Text."""
    stripped = strip_provenance(text)
    if not full_chars or len(stripped) <= full_chars:
        return 0.0
    preamble, sections, entries = (_decision_entries if is_decision else _entries)(stripped)
    fixed = _fixed_chars(preamble, sections) + _index_reserve(sections, entries, full_chars, is_decision, index_cap)
    return fixed / full_chars

