"""Inbox-Verarbeitung: validate -> Projekt bestimmen -> Mapping -> speichern.

Keine fachliche Analyse, keine Zusammenfassung, keine KI.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from . import config, mapping, snapshot, storage
from .logging_setup import setup_logging
from .schema import ValidationError, validate_and_normalize

logger = setup_logging()


@contextlib.contextmanager
def pipeline_lock() -> Iterator[bool]:
    config.ensure_dirs()
    lock_path = config.LOCK_FILE
    with lock_path.open("w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _unique_target(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    n = 2
    while True:
        candidate = directory / f"{stem}__{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def _move_to_failed(path: Path, reason: str) -> None:
    dest = _unique_target(config.FAILED_DIR, path.name)
    path.replace(dest)
    logger.error("failed file=%s reason=%s", dest.name, reason)
    _failure_reasons[dest.name] = reason


_failure_reasons: dict[str, str] = {}


def failure_reason(name: str) -> str | None:
    """Grund der Ablehnung einer Datei in diesem Prozess (fuer den Native Host)."""
    return _failure_reasons.get(name)


def _notify_conflict(detail: str) -> None:
    """Sofortige Desktop-Meldung bei abgewehrter Vermischung, hoechstens
    einmal pro Tag (Stempel im State-Verzeichnis). Der Health Check zeigt
    den Befund zusaetzlich dauerhaft an."""
    if os.environ.get("POLARIS_NO_NOTIFY") or not shutil.which("notify-send"):
        return
    stamp = config.STATE_DIR / "conflict-notified"
    today = datetime.now().date().isoformat()
    try:
        if stamp.read_text(encoding="utf-8").strip() == today:
            return
    except OSError:
        pass
    try:
        subprocess.run(
            ["notify-send", "-u", "critical", "-a", "Polaris Bridge",
             "Polaris: Nachricht im falschen Projekt abgewehrt", detail],
            check=False, timeout=10,
        )
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(today, encoding="utf-8")
    except (OSError, subprocess.SubprocessError):
        pass


def _move_to_pending(path: Path, project_id: str) -> None:
    dest_dir = config.PENDING_DIR / storage.sanitize_id(project_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    if path != dest:
        if dest.exists():
            dest = _unique_target(dest_dir, path.name)
        path.replace(dest)
    logger.info("pending project_id=%s file=%s", project_id, dest.name)


def _move_to_conflicts(path: Path, event: dict, existing: str, target: Path) -> None:
    record_conflict(event, existing, target, name=path.name)
    path.unlink()


def record_conflict(event: dict, existing: str, target: Path, name: str) -> Path:
    """Legt eine abgewehrte Vermischung mit Fundort in CONFLICTS_DIR ab (auch
    vom Export-Import genutzt). Der Curator sperrt beide Projekte, solange die
    Datei dort liegt."""
    existing_folder, _, existing_conv = existing.partition("/")
    record = dict(event)
    record["_polaris_conflict"] = {
        "existing_folder": existing_folder,
        "existing_conversation_id": existing_conv,
        "rejected_folder": target.name,
        "rejected_project_id": event["project"]["id"],
        "rejected_conversation_id": event["conversation"]["id"],
        "detected_at": datetime.now(timezone.utc).isoformat(),
    }
    dest = config.CONFLICTS_DIR / name
    if dest.exists():
        try:
            if json.loads(dest.read_text(encoding="utf-8")).get("message") == event.get("message"):
                return dest  # derselbe Konflikt schon abgelegt (z. B. nach Retry)
        except (OSError, ValueError):
            pass
        dest = _unique_target(config.CONFLICTS_DIR, name)
    storage.atomic_write_json(dest, record)
    logger.error(
        "conflict file=%s message_id=%s bereits archiviert unter %s, nicht uebernommen fuer %s/%s",
        dest.name, event["message"]["id"], existing, target.name, event["conversation"]["id"],
    )
    return dest


def _expand_snapshot(path: Path, raw: dict) -> str:
    """Chat-Abbild (seit Extension 0.4.0): rohes Unterhaltungs-JSON der
    ChatGPT-Web-Schnittstelle. Wird hier in Turn-Events zerlegt (snapshot.py)
    und als einzelne Inbox-Dateien abgelegt, die derselbe Lauf verarbeitet.

    Das Projekt kommt aus den Anbieterdaten. Der Hinweis der Extension
    (Projekt/Chat aus der Adresse) wird nur gegengeprueft: weicht er ab, wird
    nichts uebernommen - dann stimmt entweder die Seite oder die Antwort nicht."""
    conv = raw.get("conversation")
    if raw.get("source") != "chatgpt" or not isinstance(conv, dict):
        _move_to_failed(path, "invalid-snapshot: source/conversation fehlt")
        return "failed"
    hint = raw.get("hint") or {}
    provider_project = snapshot.project_id_of(conv)
    conv_id = conv.get("conversation_id") or conv.get("id")
    if hint.get("conversation_id") and conv_id and hint["conversation_id"] != conv_id:
        _move_to_failed(path, f"snapshot-mismatch: Chat {conv_id} statt {hint['conversation_id']}")
        return "failed"
    if hint.get("project_id") and provider_project and hint["project_id"] != provider_project:
        _clear_held_back(conv_id)
        _move_to_failed(
            path, f"snapshot-mismatch: Anbieter meldet Projekt {provider_project}, Seite {hint['project_id']}"
        )
        return "failed"
    if not provider_project:
        _clear_held_back(conv_id)  # nicht (mehr) in einem Projekt: nichts zu erwarten
        # Nur merken, wenn der Anbieter ausdruecklich KEIN Projekt meldet
        # (gizmo_id vorhanden, aber leer bzw. kein Projekt). Fehlt das Feld
        # ganz, hat sich eher das Antwortformat geaendert - dann muss der
        # Ersatzweg ueber die Seite offen bleiben (Review 2026-09-29, H1).
        if "gizmo_id" in conv:
            _note_no_project(conv_id, True)
        if hint.get("project_id"):
            # Laut Adresse ein Projekt-Chat, laut Anbieter keiner: vermutlich hat
            # sich das Antwortformat geaendert (gizmo_id). Nicht still verwerfen -
            # sonst stoppte die Erfassung unbemerkt (Review Runde 3, Audit 1).
            _move_to_failed(path, f"snapshot-no-project: Antwort ohne gizmo_id fuer Projekt {hint['project_id']}")
            return "failed"
        # Chat ohne Projekt: wird bewusst nie archiviert.
        path.unlink()
        return "processed"
    _note_no_project(conv_id, False)
    captured = str(raw.get("captured_at") or datetime.now(timezone.utc).isoformat())
    try:
        captured_ts = datetime.fromisoformat(captured.replace("Z", "+00:00")).timestamp()
    except ValueError:
        captured_ts = None
    # Ruhe am Erfassungszeitpunkt messen (Review Runde 5, F1).
    events = snapshot.events_from_conversation(conv, captured, capture="api", now=captured_ts)
    _note_held_back(provider_project, conv_id, conv.get("title"),
                    snapshot.last_turn_held_back(conv, captured_ts), captured)
    if snapshot.finished_assistant_nodes(conv) and not any(e["message"]["role"] == "assistant" for e in events) \
            and snapshot.is_idle(conv, captured_ts):
        # Fertige Antworten vorhanden, aber keine verwertbar: Antwortform
        # geaendert (neuer content_type o. Ae.). Sichtbar ablehnen statt nur
        # Nutzer-Nachrichten zu archivieren (Review Runde 3, Audit 2).
        _move_to_failed(path, "snapshot-shape: keine verwertbare Antwort trotz abgeschlossener Assistenten-Nachrichten")
        return "failed"
    for event in events:
        event["_polaris_from_snapshot"] = True
        name = (
            f"{storage.sanitize_id(event['project']['id'])}__{storage.sanitize_id(conv_id)}__"
            f"{storage.sanitize_id(event['message']['id'])}.json"
        )
        storage.atomic_write_json(_unique_target(config.EXPANDED_DIR, name), event)
    _note_active_branch(provider_project, conv_id, snapshot.active_message_ids(conv), captured)
    # Nur das letzte Abbild je Chat aufheben (Nachvollziehbarkeit), sonst
    # fuellte jeder Abruf processed/ mit dem ganzen Chat.
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    path.replace(config.PROCESSED_DIR / f"snapshot__{storage.sanitize_id(conv_id)}.latest.json")
    _sweep_stale_conflicts(conv_id, set((conv.get("mapping") or {}).keys()))
    logger.info("snapshot conversation_id=%s project_id=%s turns=%d", conv_id, provider_project, len(events))
    return "expanded"


NO_PROJECT_DAYS = 7


def _no_project_file() -> Path:
    return config.STATE_DIR / "no-project.json"


def _note_no_project(conv_id: str, no_project: bool) -> None:
    """Chats, fuer die der Anbieter zuletzt KEIN Projekt meldete. Liest die
    Extension danach ersatzweise aus der Seite, stammt das Projekt nur aus der
    (evtl. veralteten) Adresse - solche Seiten-Erfassungen werden abgelehnt
    statt archiviert (Architektur-Review 2026-09-29: ein aus dem Projekt
    genommener Chat landete sonst doch im Kundenordner)."""
    if not conv_id:
        return
    try:
        data = json.loads(_no_project_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if no_project:
        data[conv_id] = datetime.now(timezone.utc).isoformat()
    elif conv_id in data:
        del data[conv_id]
    else:
        return
    storage.atomic_write_json(_no_project_file(), data)


def _provider_said_no_project(conv_id: str) -> bool:
    try:
        at = json.loads(_no_project_file().read_text(encoding="utf-8")).get(conv_id)
        return bool(at) and (datetime.now(timezone.utc) - datetime.fromisoformat(at)).days < NO_PROJECT_DAYS
    except (OSError, ValueError, TypeError):
        return False


def _snapshot_ids(conv_id: str) -> set[str] | None:
    """Knoten-IDs des letzten Chat-Abbilds (Anbietersicht) oder None."""
    f = config.PROCESSED_DIR / f"snapshot__{storage.sanitize_id(conv_id)}.latest.json"
    try:
        return set((json.loads(f.read_text(encoding="utf-8")).get("conversation") or {}).get("mapping") or {})
    except (OSError, ValueError, AttributeError):
        return None


def _snapshot_captured(conv_id: str) -> str | None:
    f = config.PROCESSED_DIR / f"snapshot__{storage.sanitize_id(conv_id)}.latest.json"
    try:
        return str(json.loads(f.read_text(encoding="utf-8")).get("captured_at") or "") or None
    except (OSError, ValueError, AttributeError):
        return None


def _ts(value) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _stale_dom(event: dict, detail: tuple) -> bool:
    """Eine aus der Seite gelesene Nachricht, deren ID schon in einem ANDEREN
    Chat liegt, gilt nur dann als veraltete Seite nach einem Chatwechsel, wenn
    der Anbieter beide Seiten belegt (Review 2026-09-30, sonst Konflikt):
    1. das Abbild des angeblichen Chats ist juenger als die archivierte Kopie
       und enthaelt die Nachricht NICHT, und
    2. die archivierte Kopie kam aus einem Abbild oder das Abbild ihres Chats
       enthaelt die Nachricht.
    Derselbe Chat in anderem Projekt (verschoben) und geteilte IDs (Branch in
    neuem Chat) bleiben so sichtbare Konflikte."""
    if storage._capture(event) != "dom" or (event.get("source") or "chatgpt") != "chatgpt":
        return False
    location, existing_capture, existing_at = (tuple(detail) + (None, None))[:3]
    other_conv = location.partition("/")[2]
    claimed = event["conversation"]["id"]
    if other_conv == claimed:
        return False
    ids = {event["message"]["id"], *storage.member_ids(event)}
    own = _snapshot_ids(claimed)
    own_at, archived_at = _ts(_snapshot_captured(claimed)), _ts(existing_at)
    if own is None or ids & own or own_at is None or archived_at is None or own_at <= archived_at:
        return False
    other = _snapshot_ids(other_conv)
    return existing_capture == "api" or bool(other is not None and ids & other)


def _stale_existing(event: dict, detail: tuple) -> bool:
    """Spiegelbild von _stale_dom: die NEUE Kopie kommt aus einem Abbild
    (capture api) und das Abbild ihres Chats enthaelt sie; die ARCHIVIERTE
    liegt als Seiten-Kopie in einem anderen Chat, dessen Abbild juenger ist
    als sie und sie nicht enthaelt. Nur dann ist die archivierte die falsche."""
    if storage._capture(event) != "api" or (event.get("source") or "chatgpt") != "chatgpt":
        return False
    location, existing_capture, existing_at = (tuple(detail) + (None, None))[:3]
    other_conv = location.partition("/")[2]
    claimed = event["conversation"]["id"]
    if existing_capture != "dom" or other_conv == claimed:
        return False
    ids = {event["message"]["id"], *storage.member_ids(event)}
    own = _snapshot_ids(claimed)
    if own is None or not ids & own:
        return False
    other = _snapshot_ids(other_conv)
    other_at, archived_at = _ts(_snapshot_captured(other_conv)), _ts(existing_at)
    return (other is not None and not ids & other and other_at is not None and archived_at is not None
            and other_at > archived_at)


def _evict_stale_existing(event: dict, detail: tuple, known_paths: list[Path]) -> bool:
    """Veraltete Seiten-Kopie (samt Revisionen) aus dem anderen Chat in die
    Quarantaene und aus dessen Chat-Verzeichnis austragen. False, wenn der
    Ordner nicht erreichbar ist (dann bleibt es ein Konflikt)."""
    folder_name, _, other_conv = detail[0].partition("/")
    folder = next((p for p in known_paths if p.name == folder_name and p.is_dir()), None)
    if folder is None:
        return False
    source = event.get("source", "chatgpt")
    mdir = storage.messages_dir(folder, source)
    ids = {storage.sanitize_id(i) for i in {event["message"]["id"], *storage.member_ids(event)}}
    # Erst alles lesen und pruefen, dann verschieben (kein halber Zustand).
    todo: list[tuple[Path, str]] = []
    for mid in ids:
        for f in sorted(mdir.glob(f"{mid}.json")) + sorted(mdir.glob(f"{mid}.rev*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return False
            if (data.get("conversation") or {}).get("id") == other_conv:
                todo.append((f, data["message"]["id"]))
    if not todo:
        return False
    # Schon kuratiert? Dann nicht still ersetzen: Konflikt bleibt, damit der
    # Nutzer die Ruecknahme aus dem Wissen anstoesst (Review 2026-09-30, HIGH).
    try:
        processed = set(json.loads((folder / ".polaris" / "curator-state.json").read_text(encoding="utf-8"))
                        .get("processed_message_ids") or [])
    except (OSError, ValueError):
        processed = set()
    if any(mid in processed for _f, mid in todo):
        return False
    qdir = config.DATA_DIR / "quarantine" / f"stale-dom-{datetime.now():%Y%m%d}"
    qdir.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    for f, mid in todo:
        dest = _unique_target(qdir, f"existing__{folder.name}__{f.name}")
        shutil.move(str(f), str(dest))  # Drive -> lokal: kein rename
        moved.append(mid)
    conv_file = storage.conversations_dir(folder, source) / f"{storage.sanitize_id(other_conv)}.json"
    if moved and conv_file.is_file():
        data = json.loads(conv_file.read_text(encoding="utf-8"))
        data["known_message_ids"] = [m for m in data.get("known_message_ids", []) if m not in moved]
        data["members"] = {k: v for k, v in (data.get("members") or {}).items() if v not in moved and k not in moved}
        storage.atomic_write_json(conv_file, data)
    logger.warning("stale-dom archivierte Seiten-Kopie %s in %s durch Abbild ersetzt", ",".join(moved), detail[0])
    return bool(moved)


def _sweep_stale_conflicts(conv_id: str, snapshot_ids: set[str]) -> int:
    """Nach einem neuen Abbild: offene Konflikte, deren abgewehrte Seiten-Kopie
    laut diesem (juengeren) Abbild nicht in den Chat gehoert UND deren
    archivierte Kopie der Anbieter bestaetigt (Abbild ihres Chats enthaelt
    sie), ohne Rueckfrage in die Quarantaene. Loest z. B. den Fall vom 30.09.
    eine Minute spaeter von selbst; alles andere bleibt ein Konflikt."""
    n = 0
    if not config.CONFLICTS_DIR.is_dir():
        return 0
    for f in sorted(config.CONFLICTS_DIR.glob("*.json")):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        info = rec.get("_polaris_conflict") or {}
        msg = rec.get("message") or {}
        other_conv = info.get("existing_conversation_id")
        if (info.get("rejected_conversation_id") != conv_id or other_conv == conv_id
                or (msg.get("capture") or "dom") != "dom" or (rec.get("source") or "chatgpt") != "chatgpt"):
            continue
        ids = {msg.get("id"), *(msg.get("member_ids") or [])} - {None}
        other = _snapshot_ids(other_conv or "")
        if ids & snapshot_ids or other is None or not ids & other:
            continue
        qdir = config.DATA_DIR / "quarantine" / f"stale-dom-{datetime.now():%Y%m%d}"
        qdir.mkdir(parents=True, exist_ok=True)
        f.replace(_unique_target(qdir, f.name))
        logger.warning("stale-dom conflict=%s aufgeloest: laut Abbildern in %s, nicht in %s", f.name, other_conv, conv_id)
        n += 1
    return n


def _held_file() -> Path:
    return config.STATE_DIR / "held-back.json"


HELD_BACK_MAX_DAYS = 7


def _load_held() -> dict:
    try:
        return json.loads(_held_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _clear_held_back(conv_id: str) -> None:
    """Alle Eintraege eines Chats entfernen (egal unter welchem Projekt) -
    verschoben, aus dem Projekt genommen oder abgelehnt: sonst blieb die
    Warnung fuer immer stehen (Review Runde 6, F1)."""
    data = _load_held()
    keys = [k for k in data if k.split("/", 1)[-1] == conv_id]
    if keys:
        for k in keys:
            del data[k]
        storage.atomic_write_json(_held_file(), data)


def clear_held_back(key: str | None = None) -> int:
    """Fuer `polaris held-back --clear`: einen Eintrag oder alle entfernen."""
    data = _load_held()
    doomed = [k for k in data if key is None or k == key or k.endswith("/" + key)]
    for k in doomed:
        del data[k]
    if doomed:
        storage.atomic_write_json(_held_file(), data)
    return len(doomed)


def _note_held_back(project_id: str, conv_id: str, title, held: bool, captured: str) -> None:
    """Chats, deren letzte Antwort zurueckgehalten wird (laeuft noch oder
    endet ohne sichtbaren Text und ruhte beim Erfassen nicht). Die Bridge kann
    ohne neues Abbild nicht wissen, ob sie inzwischen fertig ist - deshalb
    nicht raten, sondern melden: der Health Check zeigt Chats, die laenger als
    1 h so stehen ("Chat oeffnen oder Sync now")."""
    data = _load_held()
    key = f"{project_id}/{conv_id}"
    # Derselbe Chat unter einem anderen Projekt (verschoben): alten Eintrag weg.
    for other in [k for k in data if k != key and k.split("/", 1)[-1] == conv_id]:
        del data[other]
    if held:
        entry = data.get(key) or {"since": captured}
        entry.update({"title": title, "last_seen": captured})
        data[key] = entry
    elif key in data:
        del data[key]
    storage.atomic_write_json(_held_file(), data)


def _note_active_branch(project_id: str, conv_id: str, active_ids: list[str], as_of: str) -> None:
    """Aktueller Strang laut letztem Abbild in der Chat-Datei des Projekts
    (active_message_ids). Der Curator uebergeht damit Turns verworfener
    Zweige (Neu-Generieren, bearbeitete Frage), die noch nicht kuratiert sind."""
    store = mapping.load_store()
    entry = store.get(project_id)
    if not entry or entry.status != "ok" or not Path(entry.local_path).is_dir():
        return
    conv_file = storage.conversations_dir(Path(entry.local_path)) / f"{storage.sanitize_id(conv_id)}.json"
    if not conv_file.is_file():
        return  # entsteht mit dem ersten Turn; beim naechsten Abbild
    data = json.loads(conv_file.read_text(encoding="utf-8"))
    # active_as_of: nur was VOR diesem Abbild erfasst wurde, kann ein
    # verworfener Zweig sein - spaeter (z. B. per DOM-Rueckfall) erfasste Turns
    # sind neuer als der vermerkte Strang (Review Runde 4, Curator F3).
    if data.get("active_message_ids") != active_ids or data.get("active_as_of") != as_of:
        data["active_message_ids"] = active_ids
        data["active_as_of"] = as_of
        storage.atomic_write_json(conv_file, data)


def _move_to_processed(path: Path) -> None:
    dest = _unique_target(config.PROCESSED_DIR, path.name)
    path.replace(dest)


def process_file(path: Path, cfg: config.Config) -> str:
    """Verarbeitet eine einzelne Inbox-/Pending-Datei. Gibt "processed",
    "pending", "failed" oder "retry" zurueck. Idempotent und unabhaengig von
    der Reihenfolge anderer Dateien aufrufbar.

    Eine einzelne Datei darf den Lauf nie abbrechen (Review 2026-09-29: eine
    Datei mit ungueltigem UTF-8 oder nicht-textueller message.id liess jeden
    Lauf scheitern, alle folgenden Dateien blieben liegen). Schreibfehler im
    Projektordner (z. B. rclone-Mount weg) lassen die Datei fuer einen
    spaeteren Versuch liegen ("retry"), statt sie nach failed/ zu schieben."""
    try:
        result = _process_file(path, cfg)
    except _RetryLater as exc:
        cause = exc.__cause__ or exc
        _note_retry(path, f"{type(cause).__name__}: {cause}")
        logger.error("retry file=%s reason=%s: %s", path.name, type(cause).__name__, cause)
        return "retry"
    except Exception as exc:  # noqa: BLE001 - s. Docstring
        if path.exists():
            _move_to_failed(path, f"error: {type(exc).__name__}: {exc}")
        _clear_retry(path)
        return "failed"
    _clear_retry(path)
    return result


class _RetryLater(Exception):
    """Fehler der Umgebung, nicht der Datei (Mount weg, projects.yaml kaputt,
    vorhandene Projektdatei unlesbar): Datei bleibt liegen. Frueher landete
    z. B. bei einer kaputten projects.yaml jedes Event dauerhaft in failed/
    (Review Runde 2, 2026-09-29)."""


def _retry_file() -> Path:
    return config.STATE_DIR / "retry.json"


def _load_retries() -> dict:
    try:
        return json.loads(_retry_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _note_retry(path: Path, reason: str) -> None:
    """Register fuer den Health Check: Dateien, die seit laengerem nur noch
    'retry' ergeben, sollen auffallen statt still liegen zu bleiben."""
    data = _load_retries()
    entry = data.get(path.name) or {"first": datetime.now(timezone.utc).isoformat(), "count": 0}
    entry["count"] = int(entry.get("count", 0)) + 1
    entry["last_error"] = reason[:300]
    entry["last"] = datetime.now(timezone.utc).isoformat()
    data[path.name] = entry
    try:
        storage.atomic_write_json(_retry_file(), data)
    except OSError:
        pass


def _clear_retry(path: Path) -> None:
    data = _load_retries()
    if path.name in data:
        del data[path.name]
        try:
            storage.atomic_write_json(_retry_file(), data)
        except OSError:
            pass


def _process_file(path: Path, cfg: config.Config) -> str:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        _move_to_failed(path, f"invalid-json: {exc}")
        return "failed"

    if isinstance(raw, dict) and raw.get("polaris_snapshot"):
        try:
            return _expand_snapshot(path, raw)
        except Exception as exc:  # noqa: BLE001 - Umgebung (Mount, Schreibfehler): spaeter erneut
            raise _RetryLater(str(exc)) from exc

    try:
        event = validate_and_normalize(raw)
    except ValidationError as exc:
        _move_to_failed(path, f"invalid-schema: {exc}")
        return "failed"

    try:
        return _store_event(path, event, cfg)
    except Exception as exc:  # noqa: BLE001 - nach der Validierung ist jeder Fehler einer der Umgebung
        raise _RetryLater(str(exc)) from exc


def _store_event(path: Path, event: dict, cfg: config.Config) -> str:
    from_snapshot = bool(event.pop("_polaris_from_snapshot", False))  # nicht in die Rohdaten
    project_id = event["project"]["id"]
    project_name = event["project"]["name"]
    conversation_id = event["conversation"]["id"]
    message_id = event["message"]["id"]
    role = event["message"]["role"]

    if (event.get("source") or "chatgpt") == "chatgpt" and storage._capture(event) == "dom" \
            and _provider_said_no_project(conversation_id):
        _move_to_failed(path, f"dom-after-no-project: laut ChatGPT gehoert Chat {conversation_id} zu keinem Projekt")
        return "failed"

    local_path, status, store = mapping.resolve_project(project_id, project_name, cfg)

    if local_path is None:
        if status == "mapped-missing-target":
            logger.warning(
                "mapping problematic project_id=%s reason=missing_target", project_id
            )
        _move_to_pending(path, project_id)
        return "pending"

    if status == "newly-mapped":
        logger.info("project mapped project_id=%s local_path=%s", project_id, local_path)

    storage.canonicalize(local_path, event)
    message_id = event["message"]["id"]
    known_paths = [Path(m.local_path) for m in store.projects.values() if m.local_path]
    detail = storage.find_conflict_detail(local_path, event, known_paths)
    conflict = detail[0] if detail else None
    while detail and _stale_existing(event, detail) and _evict_stale_existing(event, detail, known_paths):
        # Umgekehrter Fall: die schon archivierte Kopie ist die veraltete
        # Seiten-Kopie, die neue kommt aus dem Abbild des richtigen Chats
        # (2026-09-30 14:30: "bitte für heute" lag so unter einem Kundenprojekt). Die
        # Seiten-Kopie ist jetzt in der Quarantaene. Erneut pruefen - ein
        # weiterer Fundort (anderes Turn-Mitglied) waere sonst uebersehen.
        detail = storage.find_conflict_detail(local_path, event, known_paths)
        conflict = detail[0] if detail else None
    if detail and _stale_dom(event, detail):
        # Aus der Seite gelesen, aber laut Anbieter (Chat-Abbild) liegt genau
        # diese Nachricht in einem anderen Chat: veraltete Seite nach einem
        # Chatwechsel (z. B. wenn die Schnittstelle kurz HTTP 429 lieferte und
        # die Extension auf die Seite auswich). Die Anbieter-Kopie ist
        # massgeblich - die Seiten-Kopie geht ohne Rueckfrage in die
        # Quarantaene, kein Konflikt, keine Sperre (2026-09-30).
        qdir = config.DATA_DIR / "quarantine" / f"stale-dom-{datetime.now():%Y%m%d}"
        qdir.mkdir(parents=True, exist_ok=True)
        dest = _unique_target(qdir, path.name)
        storage.atomic_write_json(dest, {**event, "_polaris_stale_dom": {"archived_at": conflict}})
        path.unlink()
        logger.warning("stale-dom file=%s message_id=%s liegt laut Anbieter in %s - Seiten-Kopie verworfen (%s)",
                       path.name, message_id, conflict, dest)
        return "stale-dom"
    if conflict:
        # Welche Kopie die richtige ist, weiss die Bridge nicht (die zuerst
        # archivierte kann selbst die falsche sein). Deshalb nicht nur
        # ablehnen, sondern mit Fundort ablegen: der Curator sperrt beide
        # Projekte, bis das geklaert ist (Review 2026-09-29).
        _move_to_conflicts(path, event, conflict, local_path)
        _notify_conflict(
            f"Nachricht {message_id[:8]} liegt bereits in {conflict} und wurde nicht nach "
            f"{local_path.name} übernommen. Details: polaris-curator health"
        )
        return "conflict"

    # Direkt vor dem Schreiben: ist der Projektordner (Drive-Mount) noch da?
    # Sonst legte mkdir(parents=True) die Dateien unter dem Mountpunkt an, wo
    # sie nach dem Wiedereinhaengen verdeckt waeren (Review Runde 2).
    if not local_path.is_dir():
        raise OSError(f"Projektordner {local_path} nicht erreichbar")
    storage.write_message(local_path, event)
    storage.update_conversation_metadata(local_path, event, message_id)
    if from_snapshot:
        path.unlink()  # das Abbild selbst liegt als *.latest.json in processed/
    else:
        _move_to_processed(path)

    logger.info(
        "processed project_id=%s conversation_id=%s message_id=%s role=%s "
        "mapping=%s target=%s",
        project_id,
        conversation_id,
        message_id,
        role,
        status,
        local_path,
    )
    return "processed"


def _state_file() -> Path:
    return config.STATE_DIR / "state.json"


def _record_run() -> None:
    storage.atomic_write_json(
        _state_file(), {"last_run": datetime.now(timezone.utc).isoformat()}
    )


def last_run() -> str | None:
    f = _state_file()
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8")).get("last_run")
    except (json.JSONDecodeError, OSError):
        return None


def _count(counts: dict[str, int], result: str) -> None:
    counts[result] = counts.get(result, 0) + 1


def process_inbox() -> dict[str, int]:
    """Verarbeitet alle Dateien in der Inbox und versucht anschliessend,
    alle bereits bekannten Pending-Dateien erneut zuzuordnen (deckt sowohl
    frisches Auto-Mapping als auch `polaris map` ab)."""
    counts = {"processed": 0, "pending": 0, "failed": 0}
    with pipeline_lock() as acquired:
        if not acquired:
            logger.info("skip: another polaris process run is active")
            return counts

        config.ensure_dirs()
        cfg = config.load_config()

        # Vor dem Verarbeiten der Inbox snapshotten, damit Dateien, die in
        # diesem Lauf gerade erst neu nach Pending verschoben wurden, nicht
        # sofort im selben Lauf ein zweites Mal (erfolglos) angefasst und
        # doppelt gezaehlt werden. Sie werden beim naechsten Lauf regulaer
        # erneut versucht.
        pending_files_before = sorted(config.PENDING_DIR.glob("*/*.json"))
        store_mtime_before = _store_mtime()

        seen: set[str] = set()
        def _work() -> list[Path]:
            # Inbox und die aus Abbildern erzeugten Turn-Dateien (expanded/).
            return sorted(config.INBOX_DIR.glob("*.json")) + sorted(config.EXPANDED_DIR.glob("*.json"))

        for f in _work():
            seen.add(str(f))
            _count(counts, process_file(f, cfg))

        for f in pending_files_before:
            if f.exists():
                _count(counts, process_file(f, cfg))

        # Waehrend des Laufs angekommene Dateien gleich mitnehmen: ein
        # zweiter Aufrufer (Native Host, Path-Unit) bekam die Sperre nicht und
        # hat sich darauf verlassen, dass dieser Lauf sie sieht. Frueher
        # blieben sie bis zum naechsten Inbox-Ereignis liegen (Review Runde 2).
        for _round in range(5):
            late = [f for f in _work() if str(f) not in seen]
            if not late:
                break
            for f in late:
                seen.add(str(f))
                _count(counts, process_file(f, cfg))

        # Wurde waehrend des Laufs eine Zuordnung geaendert (Popup, CLI), die
        # wartenden Dateien noch einmal versuchen - der Aufrufer bekam die
        # Sperre nicht und verlaesst sich auf diesen Lauf (Review Runde 2).
        if _store_mtime() != store_mtime_before:
            for f in sorted(config.PENDING_DIR.glob("*/*.json")):
                result = process_file(f, cfg)
                if result != "pending":  # bereits oben als wartend gezaehlt
                    _count(counts, result)

        _record_run()

    return counts


def _store_mtime() -> float | None:
    try:
        return config.PROJECTS_FILE.stat().st_mtime
    except OSError:
        return None


def reprocess_pending_for_project(project_id: str) -> dict[str, int]:
    """busy=1 im Ergebnis: ein anderer Lauf hielt die Sperre; er verarbeitet
    die wartenden Dateien am Ende selbst nach (s. process_inbox)."""
    counts = {"processed": 0, "pending": 0, "failed": 0}
    with pipeline_lock() as acquired:
        if not acquired:
            logger.info("skip: another polaris process run is active")
            counts["busy"] = 1
            return counts
        cfg = config.load_config()
        pending_dir = config.PENDING_DIR / storage.sanitize_id(project_id)
        for f in sorted(pending_dir.glob("*.json")):
            _count(counts, process_file(f, cfg))
        _record_run()
    return counts
