"""Abgewehrte Vermischungen klaeren (seit 2026-09-29).

Liegt eine Datei in CONFLICTS_DIR, sperrt der Curator beide beteiligten
Projekte. Welche Kopie richtig ist, entscheidet der Nutzer:

- keep="existing": die archivierte Kopie ist richtig. Die abgewehrte wandert
  in die Quarantaene; die Sperre ist aufgehoben.
- keep="rejected": die abgewehrte Kopie ist richtig. Die archivierte (samt
  Revisionen) wandert in die Quarantaene und wird aus dem Chat-Verzeichnis
  entfernt; die abgewehrte wird regulaer ueber die Pipeline abgelegt. War die
  falsche Kopie schon kuratiert, meldet das Ergebnis den retract-Befehl.

Frueher hiess es nur "klaeren, dann Datei entfernen" - war die zuerst
archivierte Kopie die falsche, ging so die richtige verloren (Review Runde 3).
Nichts wird geloescht; alles Verworfene liegt unter
~/.local/share/polaris/quarantine/conflicts-<Datum>/.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import config, mapping, pipeline, storage


@dataclass
class Conflict:
    name: str
    message_id: str
    existing_folder: str
    existing_conversation_id: str
    rejected_folder: str
    rejected_conversation_id: str
    snippet: str


@dataclass
class Resolution:
    kept: str
    quarantined: list[str] = field(default_factory=list)
    hint: str | None = None


class ResolveError(Exception):
    pass


def _quarantine_dir() -> Path:
    d = config.DATA_DIR / "quarantine" / f"conflicts-{datetime.now():%Y%m%d}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_conflicts() -> list[Conflict]:
    out = []
    for path in sorted(config.CONFLICTS_DIR.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            out.append(Conflict(path.name, "?", "?", "?", "?", "?", "(unlesbar)"))
            continue
        info = record.get("_polaris_conflict") or {}
        msg = record.get("message") or {}
        out.append(Conflict(
            path.name, str(msg.get("id")), str(info.get("existing_folder")),
            str(info.get("existing_conversation_id")), str(info.get("rejected_folder")),
            str(info.get("rejected_conversation_id")), str(msg.get("content", ""))[:120].replace("\n", " "),
        ))
    return out


def _folder_path(name: str) -> Path | None:
    for m in mapping.load_store().projects.values():
        p = Path(m.local_path)
        if p.name == name and p.is_dir():
            return p
    return None


def resolve_conversation(conversation_id: str, keep: str) -> list[tuple[str, Resolution | str]]:
    """Alle Konflikte eines Chats auf einmal klaeren - typisch, wenn ein Chat
    in ChatGPT in ein anderes Projekt verschoben wurde: dann meldet jeder
    schon archivierte Turn einen Konflikt (Architektur-Review 2026-09-29).
    Betroffen sind nur Konflikte, deren abgewehrte Kopie zu diesem Chat
    gehoert UND deren archivierte Kopie im selben Chat liegt (derselbe Chat,
    anderes Projekt = verschoben). Eine Vermischung zweier Chats (Vorfall
    Kunde A/Kunde B) klaert das bewusst nicht - dort ist je Datei zu entscheiden
    (Review 2026-09-29, M1). Liefert je Datei das Ergebnis oder den
    Fehlertext."""
    if keep not in ("existing", "rejected"):
        raise ResolveError("keep muss existing oder rejected sein")
    todo = [c.name for c in list_conflicts()
            if c.rejected_conversation_id == conversation_id == c.existing_conversation_id]
    if not todo:
        raise ResolveError(f"keine Konflikte eines verschobenen Chats {conversation_id} "
                           "(Konflikte zwischen verschiedenen Chats einzeln mit --resolve klaeren)")
    out: list[tuple[str, Resolution | str]] = []
    for name in todo:
        try:
            out.append((name, resolve(name, keep)))
        except ResolveError as exc:
            out.append((name, str(exc)))
    return out


def resolve(name: str, keep: str) -> Resolution:
    if keep not in ("existing", "rejected"):
        raise ResolveError("keep muss existing oder rejected sein")
    path = config.CONFLICTS_DIR / name
    if not path.is_file():
        raise ResolveError(f"kein Konflikt {name}")
    record = json.loads(path.read_text(encoding="utf-8"))
    info = record.pop("_polaris_conflict", {}) or {}
    quarantine = _quarantine_dir()
    result = Resolution(kept=keep)

    with pipeline.pipeline_lock() as acquired:
        if not acquired:
            raise ResolveError("Bridge verarbeitet gerade - gleich erneut versuchen")
        if keep == "existing":
            dest = quarantine / f"rejected__{name}"
            path.replace(dest)
            result.quarantined.append(str(dest))
            return result

        folder = _folder_path(info.get("existing_folder", ""))
        if folder is None:
            raise ResolveError(f"Ordner {info.get('existing_folder')} nicht erreichbar")
        source = record.get("source", "chatgpt")
        msg_id = storage.sanitize_id(record["message"]["id"])
        conv_id = info.get("existing_conversation_id")
        mdir = storage.messages_dir(folder, source)
        moved = []
        for f in sorted(mdir.glob(f"{msg_id}.json")) + sorted(mdir.glob(f"{msg_id}.rev*.json")):
            existing = json.loads(f.read_text(encoding="utf-8"))
            if (existing.get("conversation") or {}).get("id") != conv_id:
                continue
            dest = quarantine / f"existing__{folder.name}__{f.name}"
            # Projektordner liegt auf dem Drive-Mount, die Quarantaene lokal:
            # rename geht nicht ueber Dateisystemgrenzen (EXDEV, 2026-09-30).
            shutil.move(str(f), str(dest))
            moved.append(str(dest))
        conv_file = storage.conversations_dir(folder, source) / f"{storage.sanitize_id(conv_id)}.json"
        if conv_file.is_file():
            data = json.loads(conv_file.read_text(encoding="utf-8"))
            data["known_message_ids"] = [m for m in data.get("known_message_ids", []) if m != record["message"]["id"]]
            data["members"] = {k: v for k, v in (data.get("members") or {}).items() if v != record["message"]["id"]}
            storage.atomic_write_json(conv_file, data)
        result.quarantined.extend(moved)
        config.INBOX_DIR.mkdir(parents=True, exist_ok=True)
        storage.atomic_write_json(config.INBOX_DIR / f"resolved__{name}", record)
        dest = quarantine / f"record__{name}"
        path.replace(dest)
        result.quarantined.append(str(dest))

    pipeline.process_inbox()
    state_file = folder / ".polaris" / "curator-state.json"
    try:
        processed = record["message"]["id"] in json.loads(state_file.read_text(encoding="utf-8")).get(
            "processed_message_ids", [])
    except (OSError, ValueError):
        processed = False
    if processed:
        result.hint = (
            f"Die falsche Kopie war in {folder.name} schon kuratiert: "
            f"polaris-curator retract {folder.name} {record['message']['id']} --force --apply --rebuild"
        )
    return result
