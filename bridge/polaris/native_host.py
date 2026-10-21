"""Native-Messaging-Host der Polaris Bridge (seit 2026-09-25).

Firefox startet dieses Programm bei Bedarf ueber runtime.connectNative und
spricht mit ihm ueber stdin/stdout (je Nachricht: 4 Byte Laenge, native
Byte-Reihenfolge, dann UTF-8-JSON). Damit entfaellt der Umweg ueber
Browser-Downloads.

Bewusst keine eigene Verarbeitungslogik: jedes Event wird atomar als Datei in
die Bridge-Inbox geschrieben (erst danach wird es bestaetigt) und sofort
ueber pipeline.process_inbox() mit derselben Pruefung, Projektzuordnung,
Pending-Behandlung und Sperre verarbeitet wie ein Download. Ist die
Verarbeitung gerade gesperrt (paralleler Lauf), bleibt die Datei in der
Inbox liegen und wird vom naechsten Lauf abgeholt - bestaetigte Events gehen
also nicht verloren.

Protokoll:
  -> {"id": <n>, "type": "ping"}
  <- {"id": <n>, "ok": true, "version": 1}
  -> {"id": <n>, "type": "export-events", "events": [<event>, ...]}
  <- {"id": <n>, "ok": true, "accepted": ["<message-id>", ...],
      "counts": {"processed": .., "pending": .., "failed": ..}}

Projektzuordnung aus dem Popup (seit 2026-09-25):
  -> {"id": <n>, "type": "list-folders", "projectId": "..."}
  <- {"id": <n>, "ok": true, "folders": ["Kunde-A", ...], "current": "Firma"|null,
      "suggested": "Firma"|null, "pending": <anzahl wartender Nachrichten>}
  -> {"id": <n>, "type": "map-project", "projectId": "...", "projectName": "...", "folder": "Firma"}
  <- {"id": <n>, "ok": true, "current": "Firma", "counts": {...}}
Waehlbar sind nur Ordner direkt unter den konfigurierten project_roots.

Das Programm beendet sich, wenn Firefox den Port schliesst (EOF) oder nach
IDLE_TIMEOUT_SECONDS ohne Nachricht.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
import os
import select
import struct
import sys
import uuid

from pathlib import Path

from . import config, mapping, pipeline, storage

PROTOCOL_VERSION = 1
IDLE_TIMEOUT_SECONDS = 120
MAX_MESSAGE_BYTES = 32 * 1024 * 1024
MAX_EVENTS_PER_MESSAGE = 1000


def _read_exact(stream, n: int) -> bytes | None:
    data = b""
    while len(data) < n:
        chunk = stream.read(n - len(data))
        if not chunk:
            return None
        data += chunk
    return data


def read_message(stream, timeout: float | None = IDLE_TIMEOUT_SECONDS):
    """Liest eine Nachricht; None bei EOF oder Leerlauf-Timeout."""
    if timeout is not None and hasattr(stream, "fileno"):
        try:
            ready, _, _ = select.select([stream], [], [], timeout)
        except (OSError, ValueError):
            ready = [stream]
        if not ready:
            return None
    header = _read_exact(stream, 4)
    if header is None:
        return None
    (length,) = struct.unpack("=I", header)
    if length > MAX_MESSAGE_BYTES:
        raise ValueError(f"Nachricht zu gross: {length} Bytes")
    body = _read_exact(stream, length)
    if body is None:
        return None
    return json.loads(body.decode("utf-8"))


def write_message(stream, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    stream.write(struct.pack("=I", len(body)))
    stream.write(body)
    stream.flush()


def _write_to_inbox(event: dict) -> str:
    """Schreibt ein Event atomar in die Inbox; liefert die Message-ID."""
    if not isinstance(event, dict):
        raise ValueError("Event ist kein Objekt")
    project = storage.sanitize_id(str((event.get("project") or {}).get("id") or "unknown"))
    conversation = storage.sanitize_id(str((event.get("conversation") or {}).get("id") or "unknown"))
    message_id = str((event.get("message") or {}).get("id") or "")
    name = f"{project}__{conversation}__{storage.sanitize_id(message_id or uuid.uuid4().hex)}.json"
    config.INBOX_DIR.mkdir(parents=True, exist_ok=True)
    target = config.INBOX_DIR / name
    tmp = config.INBOX_DIR / f".{name}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(event, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return message_id


def _folders(cfg) -> dict[str, Path]:
    folders: dict[str, Path] = {}
    for root in cfg.project_root_paths:
        try:
            entries = sorted(root.iterdir(), key=lambda p: p.name.casefold())
        except OSError:
            continue
        for p in entries:
            if p.is_dir() and not p.name.startswith("."):
                folders.setdefault(p.name, p)
    return folders


def _pending_count(project_id: str) -> int:
    d = config.PENDING_DIR / storage.sanitize_id(project_id)
    return len(list(d.glob("*.json"))) if d.is_dir() else 0


def _current_folder(project_id: str) -> str | None:
    entry = mapping.load_store().get(project_id)
    if entry and entry.status == "ok":
        return Path(entry.local_path).name
    return None


def handle_list_folders(request: dict) -> dict:
    cfg = config.load_config()
    project_id = str(request.get("projectId") or "")
    folders = _folders(cfg)
    current = _current_folder(project_id) if project_id else None
    suggested = None
    if not current and project_id.startswith("claude-p-") and cfg.suggested_folder_for_claude in folders:
        suggested = cfg.suggested_folder_for_claude
    return {
        "id": request.get("id"),
        "ok": True,
        "folders": list(folders),
        "current": current,
        "suggested": suggested,
        "pending": _pending_count(project_id) if project_id else 0,
    }


def handle_map_project(request: dict) -> dict:
    cfg = config.load_config()
    project_id = str(request.get("projectId") or "")
    folder = str(request.get("folder") or "")
    folders = _folders(cfg)
    if not project_id or folder not in folders:
        return {"id": request.get("id"), "ok": False, "error": "unbekanntes Projekt oder unzulässiger Ordner"}
    target = folders[folder]
    if not mapping.is_path_safe(target, cfg.project_root_paths):
        return {"id": request.get("id"), "ok": False, "error": "Ordner liegt ausserhalb der project_roots"}
    try:
        mapping.manual_map(project_id, target, str(request.get("projectName") or ""), confirm=bool(request.get("confirm")))
    except mapping.NeedsConfirmation as exc:
        return {"id": request.get("id"), "ok": False, "needs_confirm": True, "error": str(exc)}
    counts = pipeline.reprocess_pending_for_project(project_id)
    return {"id": request.get("id"), "ok": True, "current": folder, "counts": counts, "busy": bool(counts.get("busy"))}


def handle(request: dict) -> dict:
    req_id = request.get("id") if isinstance(request, dict) else None
    kind = request.get("type") if isinstance(request, dict) else None
    if kind == "ping":
        return {"id": req_id, "ok": True, "version": PROTOCOL_VERSION}
    if kind == "list-folders":
        return handle_list_folders(request)
    if kind == "map-project":
        return handle_map_project(request)
    if kind == "capture-status":
        # Letzter Zustand der Schnittstellen-Erfassung (Extension) fuer den
        # Health Check: {ok, error?, at}. Erfolg merkt sich den Zeitpunkt.
        status = request.get("status") if isinstance(request.get("status"), dict) else {}
        path = config.STATE_DIR / "capture-status.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        now = datetime.now(timezone.utc).isoformat()
        if status.get("ok"):
            data["last_ok"] = now
        else:
            data["last_error"] = {"at": now, "error": str(status.get("error") or "")[:200]}
        storage.atomic_write_json(path, data)
        return {"id": req_id, "ok": True}
    if kind == "export-snapshot":
        # Chat-Abbild (Extension 0.4.0): wie ein Event in die Inbox, die
        # Pipeline zerlegt es (pipeline._expand_snapshot).
        snap = request.get("snapshot")
        if not isinstance(snap, dict) or not snap.get("polaris_snapshot"):
            return {"id": req_id, "ok": False, "error": "snapshot fehlt"}
        conv = snap.get("conversation") or {}
        conv_id = storage.sanitize_id(str(conv.get("conversation_id") or conv.get("id") or "unknown"))
        config.INBOX_DIR.mkdir(parents=True, exist_ok=True)
        name = f"snapshot__{conv_id}__{uuid.uuid4().hex[:8]}.json"
        storage.atomic_write_json(config.INBOX_DIR / name, snap)
        counts = pipeline.process_inbox()
        # Abgelehnt (z. B. Projekt/Chat passt nicht, Antwortform unbekannt)?
        # Dann nicht "ok" melden - die Extension liest sonst nie aus der Seite
        # und haelt die Version fuer erledigt (Review Runde 3, M4).
        failed = config.FAILED_DIR / name
        if failed.exists():
            return {"id": req_id, "ok": False, "error": pipeline.failure_reason(name) or "Abbild abgelehnt",
                    "counts": counts}
        return {"id": req_id, "ok": True, "accepted_snapshot": conv_id, "counts": counts,
                "queued": (config.INBOX_DIR / name).exists()}
    if kind == "export-events":
        events = request.get("events")
        if not isinstance(events, list) or len(events) > MAX_EVENTS_PER_MESSAGE:
            return {"id": req_id, "ok": False, "error": "events fehlt oder zu viele"}
        accepted: list[str] = []
        errors: list[str] = []
        for event in events:
            try:
                accepted.append(_write_to_inbox(event))
            except Exception as exc:  # noqa: BLE001 - einzelnes Event darf den Rest nicht blockieren
                errors.append(str(exc))
        counts = pipeline.process_inbox() if accepted else {}
        reply = {"id": req_id, "ok": not errors, "accepted": accepted, "counts": counts}
        if errors:
            reply["error"] = "; ".join(errors[:5])
        return reply
    return {"id": req_id, "ok": False, "error": f"unbekannter Typ: {kind!r}"}


def main() -> int:
    # Ungepuffert lesen: select() schaut auf den Dateideskriptor. Ein
    # gepufferter Leser hatte die zweite, direkt folgende Nachricht schon in
    # seinen Puffer geholt - select meldete dann nichts, und die Anfrage blieb
    # bis zum Leerlauf-Timeout unbeantwortet (Review 2026-09-29).
    stdin = sys.stdin.buffer.raw
    stdout = sys.stdout.buffer
    while True:
        try:
            request = read_message(stdin)
        except (ValueError, json.JSONDecodeError) as exc:
            write_message(stdout, {"id": None, "ok": False, "error": str(exc)})
            return 1
        if request is None:
            return 0
        try:
            reply = handle(request)
        except Exception as exc:  # noqa: BLE001 - Host soll dem Browser immer antworten
            reply = {"id": request.get("id") if isinstance(request, dict) else None, "ok": False, "error": str(exc)}
        write_message(stdout, reply)


if __name__ == "__main__":
    sys.exit(main())
