"""Liest neue ChatGPT-Message-Events aus raw/chatgpt/messages/, die die
Bridge dort bereits abgelegt hat. Der Curator veraendert diese Dateien
niemals."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import layout, state as state_mod


@dataclass
class ChatMessage:
    message_id: str
    conversation_id: str
    conversation_title: str | None
    role: str
    content: str
    parent_id: str | None
    source_timestamp: str | None
    captured_at: str
    local_date: str  # YYYY-MM-DD, lokale Zeitzone
    source: str = "chatgpt"  # "chatgpt" | "claude"
    revision: int = 1  # hoechste verwendete .revN (1 = Originaldatei)
    fed_keys: list | None = None  # Pruefsummen der an das Modell gegebenen Absaetze
    project_id: str | None = None  # project.id laut Erfassung (Anbieter)


def _effective_timestamp(event: dict) -> str:
    msg = event.get("message", {})
    return msg.get("source_timestamp") or event.get("captured_at")


def _to_local_date(iso_ts: str) -> str:
    """Wandelt einen ISO-8601-Zeitstempel (i.d.R. UTC, von captured_at) in
    das lokale Kalenderdatum (YYYY-MM-DD) dieses Rechners um. source_
    timestamp ist in der aktuellen Extension-Version immer None; captured_
    at ist daher der praktische Zeitanker fuer die Tagesbatch-Bildung."""
    ts = iso_ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        # Kein Offset angegeben: als bereits lokal behandeln.
        return dt.date().isoformat()
    local_dt = dt.astimezone()
    return local_dt.date().isoformat()


def load_message(path: Path) -> ChatMessage | None:
    try:
        event = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError):
        return None  # die Integritaetspruefung meldet unlesbare Dateien
    if not isinstance(event, dict):
        return None

    msg = event.get("message", {}) or {}
    conv = event.get("conversation", {})
    message_id = msg.get("id")
    conversation_id = conv.get("id")
    captured_at = event.get("captured_at")
    if not message_id or not conversation_id or not captured_at:
        return None

    ts = _effective_timestamp(event)
    content = msg.get("content", "")
    uploads = [str(a) for a in (msg.get("attachments") or []) if a]
    if uploads:
        # Seit Extension 0.1.2: Namen im Chat hochgeladener Dateien (ohne Inhalt).
        content = f"{content}\n[Im Chat hochgeladene Datei(en): {', '.join(uploads)}]"
    return ChatMessage(
        message_id=message_id,
        conversation_id=conversation_id,
        conversation_title=conv.get("title"),
        role=msg.get("role", "unknown"),
        content=content,
        parent_id=msg.get("parent_id"),
        source_timestamp=msg.get("source_timestamp"),
        captured_at=captured_at,
        local_date=_to_local_date(ts),
        source=event.get("source") or "chatgpt",
        project_id=(event.get("project") or {}).get("id") if isinstance(event.get("project"), dict) else None,
    )


def _revision_number(path: Path) -> int:
    """m.json -> 1, m.rev2.json -> 2 (Revisionen legt die Bridge an)."""
    stem = path.stem
    if ".rev" in stem:
        try:
            return int(stem.rsplit(".rev", 1)[1])
        except ValueError:
            return 1
    return 1


# ---- Abdeckung: jeder Text eines Chats wird genau einmal kuratiert ----
# (seit Review Runde 3, 2026-09-29; ersetzt die Fortsetzungs-Sonderlogik)
#
# Derselbe Inhalt kann mehrfach im Rohbestand stehen: per DOM und per
# Schnittstelle unter verschiedenen IDs, als Revision mit anderer Formatierung,
# als abgeschnittene und spaeter vollstaendige Fassung, aus dem Export. Statt
# das an den IDs zu entscheiden, gilt je Chat: ein Absatz, dessen
# normalisierter Text (nur Buchstaben/Ziffern) schon in einer kuratierten
# Fassung dieses Chats vorkommt, geht nicht noch einmal an das Modell.

MIN_NEW_CHARS = 100  # weniger neuer Text in einer Revision: kosmetisch
_PARA_SPLIT_RE = re.compile(r"\n\s*\n")
_NON_WORD_RE = re.compile(r"[\W_]+", re.UNICODE)
_MD_LINK_TARGET_RE = re.compile(r"\]\([^)]*\)")
_CAPTURE_NOISE_RE = re.compile(r"\ue200?cite\S*|cite(?:turn\d+\w*)+|\b(?:Copy code|Code kopieren|Kopieren)\b", re.IGNORECASE)


def _comparable(text: str) -> str:
    """Wie bridge storage.comparable."""
    text = _CAPTURE_NOISE_RE.sub(" ", _MD_LINK_TARGET_RE.sub("]", text or ""))
    return _NON_WORD_RE.sub("", text).casefold()


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in _PARA_SPLIT_RE.split(text or "") if p.strip()]


def _ts(value: str | None) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return float("-inf")


def paragraph_hash(key: str) -> str:
    import hashlib
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def legacy_migration(project: Path, state) -> tuple[dict[str, int], dict[str, list]]:
    """Einmalige Migration aelterer States (Review Runden 5/6).

    Frueher kamen alle Fassungen einer noch unverarbeiteten Nachricht in einen
    Batch - die ID steht dann je Fassung einmal in derselben Herkunftsliste.
    Spaeter eingetroffene Fassungen einer schon verarbeiteten Nachricht sah das
    Modell nie. Daraus:
    - eingefrorene Fassung = wie oft die ID in EINER Herkunftsliste steht
      (mindestens 1); keine Zeitgrenze.
    - Die gesendeten Absaetze werden ausdruecklich festgeschrieben (Absaetze der
      Fassungen bis dahin) - danach gibt es keinen impliziten "alles
      gesendet"-Zustand mehr, den spaetere Aufrufe mit [] ueberschreiben
      koennten (Review Runde 6, F1).
    - Verarbeitet, aber in keiner Herkunftsangabe: hat nichts beigetragen ([])."""
    revisions: dict[str, int] = {}
    fed: dict[str, list] = {}
    # Je unterschiedlicher Herkunftsliste (= ein Batch) zaehlen, wie oft die ID
    # vorkommt, und ueber alle Batches aufsummieren: der alte Code verteilte
    # Fassungen einer Nachricht auch auf mehrere Tages-/Teil-Batches (Review
    # Runde 7). Dieselbe Liste an mehreren Eintraegen zaehlt nur einmal.
    per_list_max: dict[str, int] = {}
    lists: set[str] = set()
    for name in ("knowledge.md", "decisions.md", "open-loops.md", "knowledge-archiv.md"):
        path = project / name
        if not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="replace")  # kaputtes Byte: kein Abbruch
        # Je Herkunftsangabe der ganze Batch (batch_ids, seit 2026-09-29),
        # sonst message_ids - Teilmengen je Aussage sind keine eigenen Batches.
        for src in re.finditer(r"<!-- polaris-source:(.*?)-->", text):
            m = re.search(r"\bbatch_ids=(\S+)", src.group(1)) or re.search(r"\bmessage_ids=(\S+)", src.group(1))
            if m:
                lists.add(m.group(1).rstrip("-"))
    for lst in lists:
        for i in lst.split(","):
            if i:
                per_list_max[i] = per_list_max.get(i, 0) + 1
    files_of: dict[str, list[tuple[int, str]]] = {}
    for _src, directory in layout.chat_messages_dirs(project):
        if not directory.is_dir():
            continue
        for path in directory.glob("*.json"):
            msg = load_message(path)
            if msg is None or not state.is_message_processed(msg.message_id):
                continue
            files_of.setdefault(msg.message_id, []).append((_revision_number(path), msg.content))
    for mid, files in files_of.items():
        if mid not in state.processed_revisions:
            revisions[mid] = max(1, min(per_list_max.get(mid, 1), max(r for r, _c in files)))
        if mid in state.fed_paragraphs:
            continue
        if mid not in per_list_max:
            fed[mid] = []
            continue
        done = revisions.get(mid, int(state.processed_revisions.get(mid, 1)))
        fed[mid] = sorted({
            paragraph_hash(_comparable(p)) for r, content in files if r <= done for p in _paragraphs(content)
        })
    return revisions, fed


@dataclass
class Absorbed:
    """Ohne Modellaufruf als verarbeitet zu vermerken (Inhalt schon abgedeckt)."""
    message_id: str
    local_date: str
    revision: int


@dataclass
class _File:
    path: Path
    rev: int
    msg: ChatMessage
    mtime: float


def _fed_revision(state, mid: str, files: list[_File]) -> int:
    """Hoechste kuratierte Fassung einer verarbeiteten Nachricht. Ohne Angabe
    (aeltere Laeufe) nur die erste - s. legacy_migration."""
    return int(state.processed_revisions.get(mid, 1))


def allowed_project_ids(project: Path) -> set[str] | None:
    """project.ids, die laut projects.yaml (status ok) genau diesem Ordner
    zugeordnet sind. None = keine Zuordnungen bekannt (frische Installation,
    Tests): dann wird nicht gefiltert."""
    from . import config

    raw = config._read_yaml(config.PROJECTS_FILE).get("projects") or {}
    # missing_target: die Bridge fand den Ordner voruebergehend nicht (Drive
    # weg) - die Zuordnung gilt weiter (Review 2026-09-29, L1).
    mappings = {pid: Path(e["local_path"]) for pid, e in raw.items()
                if isinstance(e, dict) and e.get("local_path") and e.get("status") in ("ok", "missing_target")}
    if not mappings:
        return None
    here = project.resolve()
    return {pid for pid, path in mappings.items() if path.resolve() == here}


def load_new_messages(project: Path, state: state_mod.CuratorState,
                      absorbed: list[Absorbed] | None = None,
                      allowed_pids: set[str] | None = None,
                      unmapped: list[str] | None = None) -> list[ChatMessage]:
    """Neue Chat-Inhalte zum Kuratieren. absorbed sammelt Nachrichten, deren
    Inhalt schon vollstaendig abgedeckt ist - der Aufrufer vermerkt sie als
    verarbeitet, ohne das Modell zu fragen.

    allowed_pids: nur Nachrichten, deren project.id diesem Ordner zugeordnet
    ist, werden kuratiert (seit 2026-09-29). Sonst kuratierte der Curator nach
    einer korrigierten Fehlzuordnung (unmap) die im Ordner liegengebliebenen
    Nachrichten des fremden Projekts einfach mit - Kundenvermischung
    (Architektur-Review). Solche Nachrichten bleiben unverarbeitet liegen
    (unmapped sammelt ihre IDs, der Health Check meldet sie)."""
    retracted = set(getattr(state, "retracted_ids", set()) or set())
    by_conv: dict[str, dict[str, list[_File]]] = {}
    titles: dict[str, str | None] = {}
    active: dict[str, tuple[set[str], str | None]] = {}
    for source, directory in layout.chat_messages_dirs(project):
        if not directory.is_dir():
            continue
        conv_dir = directory.parent / "conversations"
        for path in sorted(directory.glob("*.json")):
            msg = load_message(path)
            if msg is None:
                continue
            if allowed_pids is not None and msg.project_id not in allowed_pids:
                if unmapped is not None and msg.message_id not in unmapped:
                    unmapped.append(msg.message_id)
                continue
            msg.revision = _revision_number(path)
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            by_conv.setdefault(msg.conversation_id, {}).setdefault(msg.message_id, []).append(
                _File(path, msg.revision, msg, mtime)
            )
            titles.setdefault(msg.conversation_id, msg.conversation_title)
            if msg.conversation_id not in active:
                cf = conv_dir / f"{msg.conversation_id}.json"
                ids: set[str] = set()
                as_of = None
                if cf.is_file():
                    try:
                        cdata = json.loads(cf.read_text(encoding="utf-8"))
                        ids = set(cdata.get("active_message_ids") or [])
                        as_of = cdata.get("active_as_of")
                    except (OSError, ValueError, UnicodeDecodeError):
                        ids = set()
                active[msg.conversation_id] = (ids, as_of)

    result: list[ChatMessage] = []
    for conv_id, messages in by_conv.items():
        # Was dieser Chat schon kuratiert hat.
        covered_long = []
        for mid, files in messages.items():
            if not state.is_message_processed(mid):
                continue
            done = _fed_revision(state, mid, files)
            fed = state.fed_paragraphs.get(mid)
            fed_set = set(fed) if fed is not None else None
            for f in files:
                if f.rev <= done:
                    for p in _paragraphs(f.msg.content):
                        key = _comparable(p)
                        if fed_set is None or paragraph_hash(key) in fed_set:
                            covered_long.append(key)
        covered_text = "\x00".join(covered_long)
        by_id = {mid: max(files, key=lambda f: f.rev).msg for mid, files in messages.items()}

        def is_covered(key: str) -> bool:
            # Kurze Absaetze ("ja", "ok, bitte") zaehlen nie als abgedeckt - sie
            # sind Kontext fuer die folgende Antwort und kosten nichts.
            return len(key) >= 20 and key in covered_text

        for mid, files in messages.items():
            if mid in retracted:
                continue  # zurueckgenommen: nie (wieder) kuratieren
            processed = state.is_message_processed(mid)
            done = _fed_revision(state, mid, files) if processed else 0
            candidates = [f for f in files if f.rev > done]
            if not candidates:
                continue
            # Die inhaltsreichste noch nicht kuratierte Fassung, nicht einfach die
            # hoechste Nummer: eine spaeter abgeschnittene Erfassung verdraengte
            # sonst die vollstaendige (Review Runde 3). Gleichstand: neuere.
            newest = max(candidates, key=lambda f: (len(_comparable(f.msg.content)), f.rev))
            record_rev = max(f.rev for f in files)
            ids_on_branch, as_of = active.get(conv_id) or (set(), None)
            if ids_on_branch and mid not in ids_on_branch and not processed and (
                as_of is None or _ts(newest.msg.captured_at) <= _ts(as_of)
            ):
                continue  # verworfener Zweig (Neu-Generieren/Bearbeiten); kommt ggf. zurueck
            text = newest.msg.content
            if processed:
                fed = max((f for f in files if f.rev <= _fed_revision(state, mid, files)), key=lambda f: f.rev, default=None)
                if fed is not None and text.startswith(fed.msg.content):
                    text = text[len(fed.msg.content):]  # woertliche Fortsetzung: nur der Rest
            paras = _paragraphs(text)
            fresh = [p for p in paras if not is_covered(_comparable(p))]
            fresh_chars = sum(len(_comparable(p)) for p in fresh)
            # Still uebernehmen nur, wenn wirklich nichts Neues dazukam: kein
            # neuer Absatz mit >= 20 Zeichen und zusammen < MIN_NEW_CHARS.
            # Vorher ging ein kurzer, spaeter nachgelieferter Schluss wie
            # "Empfehlung: Angebot B, Frist 15.10., 12.500 EUR" verloren
            # (Architektur-Review 2026-09-29).
            substantial = any(len(_comparable(p)) >= 20 for p in fresh)
            if not fresh or (processed and fresh_chars < MIN_NEW_CHARS and not substantial):
                if absorbed is not None:
                    absorbed.append(Absorbed(mid, newest.msg.local_date, record_rev))
                continue
            m = newest.msg
            m.revision = record_rev  # alle vorhandenen Fassungen gelten danach als kuratiert
            m.fed_keys = sorted({paragraph_hash(_comparable(p)) for p in fresh})
            if len(fresh) < len(paras) or processed:
                parent = by_id.get(m.parent_id) if m.parent_id else None
                context = f" auf die Frage: {parent.content[:300]!r}" if parent and parent.role == "user" else ""
                m.content = (
                    f"[Teil einer Nachricht im Chat {titles.get(conv_id)!r}{context}. "
                    "Bereits verarbeitete Absaetze sind ausgelassen - nur diese neuen:]\n"
                    + "\n\n".join(fresh)
                )
            result.append(m)

    result.sort(key=lambda m: (m.local_date, m.captured_at, m.conversation_id))
    return result
