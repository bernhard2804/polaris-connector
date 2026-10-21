"""Zwischenspeicher fuer `status-all` (seit 2026-10-09).

Das Widget fragte alle 2 min `status-all` ab; das machte je Projekt einen
vollstaendigen Probelauf (alle Nachrichten lesen, Dokumente hashen und
extrahieren). Auf dem rclone-Laufwerk kostete das rund 2 s eigene und 4 s
rclone-Rechenzeit je Aufruf und erzeugte Lastspitzen bis 173 % eines Kerns
(Energie-Diagnose 2026-10-09).

Ein Projekt wird nur neu berechnet, wenn sich sein Fingerabdruck aendert:
Dateilisten (Name, Groesse, Aenderungszeit) der Chat- und Inbox-Ordner,
Curator-Zustand, Journal, Konfigurationen, Projektzuordnung und das Datum.
Unabhaengig davon spaetestens nach MAX_AGE. Nur Verzeichniseintraege werden
gelesen, keine Inhalte - auf dem rclone-Laufwerk kommt das aus dem
Verzeichnis-Cache.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import date
from pathlib import Path

from . import config as config_mod
from . import journal, layout

CACHE_FILE = Path.home() / ".local" / "state" / "polaris" / "status-cache.json"
MAX_AGE = 30 * 60  # Sekunden


def _entries(directory: Path) -> list:
    out = []
    try:
        with os.scandir(directory) as it:
            for e in it:
                try:
                    if e.is_file():
                        st = e.stat()
                        out.append((e.name, st.st_size, st.st_mtime_ns))
                except OSError:
                    continue
    except OSError:
        return [("<fehlt>", 0, 0)]
    return sorted(out)


def _stat(path: Path) -> tuple:
    try:
        st = path.stat()
        return (st.st_size, st.st_mtime_ns)
    except OSError:
        return (None, None)


def fingerprint(project: Path) -> str:
    parts: list = [date.today().isoformat()]
    for _src, d in layout.chat_messages_dirs(project):
        parts.append((str(d.relative_to(project)), _entries(d)))
    for kind in ("mail", "documents", "notes"):
        parts.append((kind, _entries(layout.inbox_dir(project, kind))))
    polaris = layout.polaris_dir(project)
    for name in ("curator-state.json", "journal.json", "curator-config.yaml"):
        parts.append((name, _stat(polaris / name)))
    parts.append(("journal-lokal", _stat(journal.path(project))))
    parts.append(("global", _stat(config_mod.GLOBAL_CONFIG_FILE)))
    parts.append(("projects", _stat(config_mod.PROJECTS_FILE)))
    return hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()


def _load() -> dict:
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, CACHE_FILE)
    except OSError:
        pass  # Zwischenspeicher ist entbehrlich


def payloads(projects: list[tuple[str | None, Path]], compute, now: float | None = None) -> list[dict]:
    """compute(project_id, path) -> dict wird nur fuer geaenderte Projekte aufgerufen."""
    now = now if now is not None else time.time()
    cache = _load()
    result, fresh = [], {}
    for project_id, path in projects:
        key = str(path)
        fp = fingerprint(path)
        hit = cache.get(key)
        if isinstance(hit, dict) and hit.get("fp") == fp and now - hit.get("at", 0) < MAX_AGE:
            payload = hit["payload"]
        else:
            payload = compute(project_id, path)
            hit = {"fp": fp, "at": now, "payload": payload}
        fresh[key] = hit
        result.append(payload)
    _save(fresh)
    return result
