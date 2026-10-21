"""Kanonische Ablage von Message Events und Conversation-Metadaten.

Prinzipien:
- Atomare Schreibvorgaenge (tmp + fsync + rename).
- Messages sind grundsaetzlich immutable. Taucht dieselbe message-id mit
  verandertem Inhalt erneut auf, wird eine Revision angelegt statt den
  Original-Datensatz zu ueberschreiben.
- IDs, niemals Namen, werden als Dateinamen verwendet.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]")


def sanitize_id(value: str) -> str:
    value = value.strip()
    value = _SAFE_ID.sub("_", value)
    return value[:200] if value else "unknown"


def atomic_write_text(path: Path, text: str) -> None:
    """Schreibt atomar (tmp + fsync + rename). Der Temp-Name ist je Prozess
    und Aufruf eindeutig: ein fester Name (<datei>.tmp) konnte zwischen zwei
    Schreibern (Pipeline, Import, Native Host) kollidieren, und auf dem
    Drive-Mount wurde er mitsynchronisiert (Review 2026-09-29)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        # Zeitstempel ausdruecklich setzen: Auf dem rclone-Drive-Mount blieb nach
        # einem gescheiterten Upload (2026-10-04) die alte Aenderungszeit am
        # Objekt haengen, obwohl der Inhalt spaeter ersetzt wurde - eine knowledge.md
        # sah tagelang "seit 04.10. unveraendert" aus (2026-10-08).
        os.utime(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def atomic_write_json(path: Path, data: dict) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False))


# Seit 2026-09-25 zwei Quellen: ChatGPT (raw/chatgpt/) und Claude (raw/claude/).
SOURCES = ("chatgpt", "claude")


def _source_dir(source: str) -> str:
    return source if source in SOURCES else "chatgpt"


def messages_dir(project_path: Path, source: str = "chatgpt") -> Path:
    return project_path / "raw" / _source_dir(source) / "messages"


def conversations_dir(project_path: Path, source: str = "chatgpt") -> Path:
    return project_path / "raw" / _source_dir(source) / "conversations"


def _next_revision_path(base_path: Path) -> Path:
    n = 2
    while True:
        candidate = base_path.with_name(f"{base_path.stem}.rev{n}{base_path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def member_ids(event: dict) -> list[str]:
    msg = event.get("message") or {}
    ids = [str(m) for m in (msg.get("member_ids") or []) if isinstance(m, str) and m.strip()]
    return ids or [msg["id"]]


def canonicalize(project_path: Path, event: dict) -> None:
    """Legt fest, unter welcher ID ein Turn abgelegt wird. Nennt das Event
    mehrere Nachrichten-IDs (member_ids, seit 2026-09-29) und ist eine davon in
    diesem Chat schon bekannt - im Chat-Verzeichnis oder als Datei -, gilt
    deren ID. So landet derselbe Turn nie doppelt, egal ob ihn DOM (erste ID
    der Such-Einheit), Schnittstelle oder Export (frueheste Nachricht) liefert."""
    msg = event["message"]
    members = member_ids(event)
    source = event.get("source", "chatgpt")
    conv_id = event["conversation"]["id"]
    conv_file = conversations_dir(project_path, source) / f"{sanitize_id(conv_id)}.json"
    known: dict = {}
    if conv_file.is_file():
        known = (json.loads(conv_file.read_text(encoding="utf-8")).get("members") or {})
    # Auch bei nur einer ID nachsehen: eine DOM-Einheit nennt oft nur die
    # sichtbare Nachricht, das Abbild fuehrt den Turn unter seiner fruehesten
    # (Review Runde 3, H1).
    canonical = next((known[m] for m in members if m in known), None)
    if canonical is None:
        for m in members:
            f = messages_dir(project_path, source) / f"{sanitize_id(m)}.json"
            if not f.is_file():
                continue
            existing = json.loads(f.read_text(encoding="utf-8"))
            # Nur derselbe Chat und dieselbe Rolle (Review Runde 3, H3).
            if (existing.get("conversation") or {}).get("id") == conv_id and (
                (existing.get("message") or {}).get("role") == msg.get("role")
            ):
                canonical = m
                break
    if canonical and canonical != msg["id"]:
        msg["provider_id"] = msg["id"]
        msg["id"] = canonical


def find_conflicting_message(
    project_path: Path, event: dict, known_project_paths: list[Path]
) -> str | None:
    found = find_conflict_detail(project_path, event, known_project_paths)
    return found[0] if found else None


def find_conflict_detail(
    project_path: Path, event: dict, known_project_paths: list[Path]
) -> tuple[str, str, str | None] | None:
    """Prueft, ob dieselbe message-id schon in einem anderen Projekt oder
    unter einer anderen Conversation archiviert ist. Gibt dann
    ("<Ordner>/<conversation-id>", Erfassungsart der archivierten Kopie,
    deren captured_at) zurueck, sonst None.

    Hintergrund (2026-09-29): Beim Wechsel zwischen zwei Chats exportierte
    die Extension die noch sichtbaren Nachrichten des alten Chats unter der
    neuen Adresse; Nachrichten zweier Kundenprojekte landeten so im jeweils
    anderen Projekt. Native IDs sind bei ChatGPT und Claude global
    eindeutig - dieselbe ID an einem zweiten Ort ist ein Erfassungsfehler,
    kein neuer Inhalt."""
    conv_id = event["conversation"]["id"]
    source = event.get("source", "chatgpt")
    own = project_path.resolve()
    candidates: list[Path] = []
    seen: set[Path] = set()
    for candidate in [project_path, *known_project_paths]:
        resolved = candidate.resolve()
        if resolved not in seen:
            seen.add(resolved)
            candidates.append(candidate)
    # Alle Bestandteile des Turns pruefen, nicht nur die Anzeige-ID.
    for mid in {sanitize_id(m) for m in [event["message"]["id"], *member_ids(event)]}:
        for candidate in candidates:
            existing_path = messages_dir(candidate, source) / f"{mid}.json"
            if not existing_path.is_file():
                continue
            # Unlesbar heisst nicht "kein Konflikt": sonst entschied ein
            # Lesefehler auf dem Drive-Mount ueber die Zuordnung (Review
            # Runde 2). Der Aufrufer versucht es spaeter erneut.
            existing = json.loads(existing_path.read_text(encoding="utf-8"))
            existing_conv = (existing.get("conversation") or {}).get("id")
            if candidate.resolve() != own or existing_conv != conv_id:
                return f"{candidate.name}/{existing_conv}", _capture(existing), existing.get("captured_at")
    return None


def _content_already_stored(base: Path, event: dict) -> bool:
    content = (event.get("message") or {}).get("content")
    for rev in base.parent.glob(f"{base.stem}.rev*{base.suffix}"):
        try:
            if json.loads(rev.read_text(encoding="utf-8")).get("message", {}).get("content") == content:
                return True
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue
    return False


_MD_LINK_TARGET_RE = re.compile(r"\]\([^)]*\)")
# Artefakte, die nur eine der Erfassungsarten enthaelt: Zitat-Marken der
# Schnittstelle, Knopftexte des DOM (Review Runde 3, M5).
_CAPTURE_NOISE_RE = re.compile(r"\ue200?cite\S*|cite(?:turn\d+\w*)+|\b(?:Copy code|Code kopieren|Kopieren)\b", re.IGNORECASE)
_NON_WORD_RE = re.compile(r"[\W_]+", re.UNICODE)


def comparable(text: str) -> str:
    """Text ohne Formatierung: Markdown-Linkziele weg, nur Buchstaben und
    Ziffern, klein. DOM-innerText und Markdown derselben Antwort werden so
    gleich."""
    text = _CAPTURE_NOISE_RE.sub(" ", _MD_LINK_TARGET_RE.sub("]", text or ""))
    return _NON_WORD_RE.sub("", text).casefold()


def _capture(event: dict) -> str:
    """Erfassungsart einer ChatGPT-Nachricht; aeltere Events ohne Angabe
    stammen aus dem DOM."""
    return (event.get("message") or {}).get("capture") or "dom"


def write_message(project_path: Path, event: dict) -> Path:
    """Schreibt die kanonische Message-Datei. Gibt den geschriebenen Pfad
    zurueck. Bei identischem, bereits vorhandenem Inhalt wird nichts erneut
    geschrieben (Idempotenz). Bei abweichendem Inhalt unter derselben ID
    wird eine Revision angelegt, das Original bleibt unveraendert."""
    msg_id = sanitize_id(event["message"]["id"])
    target = messages_dir(project_path, event.get("source", "chatgpt")) / f"{msg_id}.json"

    if target.exists():
        # Unlesbar -> Fehler (Aufrufer versucht es spaeter erneut) statt
        # blind eine Revision anzulegen (Review Runde 2).
        existing = json.loads(target.read_text(encoding="utf-8"))
        if existing == event:
            return target
        if existing is not None and existing.get("message", {}).get("content") == event.get(
            "message", {}
        ).get("content"):
            # Nur Metadaten unterscheiden sich (z.B. captured_at) - kein
            # inhaltlicher Unterschied, keine Revision noetig.
            return target
        if _content_already_stored(target, event):
            # Gleicher Text wie eine vorhandene Revision: sonst entstanden bei
            # partial -> final -> final beliebig viele gleiche .revN (Review
            # 2026-09-29).
            return target
        if existing is not None and _capture(existing) != _capture(event) and comparable(
            existing.get("message", {}).get("content", "")
        ) == comparable(event.get("message", {}).get("content", "")):
            # Seit Extension 0.3.0 (2026-09-29) kann ChatGPT ueber das DOM
            # (innerText) oder die Web-Schnittstelle bzw. den Export (Markdown)
            # erfasst werden. Derselbe Text sieht dann unterschiedlich aus - das
            # ist keine Bearbeitung. Nur bei inhaltlicher Gleichheit keine
            # Revision: eine per DOM abgeschnittene Antwort, die spaeter
            # vollstaendig kommt, MUSS als Revision erhalten bleiben (Review
            # Runde 2, 2026-09-29).
            return target
        revision_path = _next_revision_path(target)
        atomic_write_json(revision_path, event)
        return revision_path

    atomic_write_json(target, event)
    return target


def update_conversation_metadata(project_path: Path, event: dict, message_id: str) -> Path:
    conv = event["conversation"]
    conv_id = sanitize_id(conv["id"])
    target = conversations_dir(project_path, event.get("source", "chatgpt")) / f"{conv_id}.json"
    now = datetime.now(timezone.utc).isoformat()

    # Unlesbar -> Fehler (retry, im Health sichtbar) statt still neu anlegen:
    # das loeschte known_message_ids und die Mitglieder-Tabelle (Review Runde 3).
    data = json.loads(target.read_text(encoding="utf-8")) if target.exists() else None

    before = json.dumps(data, sort_keys=True) if data is not None else None
    if data is None:
        data = {
            "schema_version": 1,
            "source": event.get("source", "chatgpt"),
            "project_id": event["project"]["id"],
            "conversation_id": conv["id"],
            "title": conv.get("title"),
            "url": conv.get("url"),
            "first_seen": now,
            "last_seen": now,
            "known_message_ids": [],
        }

    if conv.get("title"):
        data["title"] = conv["title"]
    if conv.get("url"):
        data["url"] = conv["url"]
    data["last_seen"] = now
    if message_id not in data["known_message_ids"]:
        data["known_message_ids"].append(message_id)
    members = member_ids(event)
    # Nur Abbilder bzw. Export (capture=api) beschreiben die Mitglieder-Tabelle:
    # ihre Gruppierung kommt aus den Anbieterdaten. Die Gruppierung der DOM-
    # Einheiten ist unbestaetigt - fasste eine Einheit je zwei Turns zusammen,
    # landete sonst ein spaeterer Turn unter einer falschen ID (Review Runde 3, H3).
    if _capture(event) == "api" and (len(members) > 1 or members[0] != message_id):
        table = data.setdefault("members", {})
        for m in members:
            table.setdefault(m, message_id)

    # Nur schreiben, wenn sich etwas geaendert hat (last_seen allein zaehlt
    # nicht) - jedes Abbild schrieb sonst je Turn die Chat-Datei auf den
    # Drive-Mount neu (Review Runde 3, L1).
    if before is not None:
        probe = dict(data)
        probe["last_seen"] = json.loads(before).get("last_seen")
        if json.dumps(probe, sort_keys=True) == before:
            return target
    atomic_write_json(target, data)
    return target
