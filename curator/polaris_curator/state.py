"""Curator-State: welche Messages/Dateien wurden bereits verarbeitet.

State wird ausschliesslich nach erfolgreichem Merge aktualisiert (siehe
pipeline.py) - ein fehlgeschlagener Lauf darf niemals etwas als
verarbeitet markieren.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import layout, storage

SCHEMA_VERSION = 1


@dataclass
class DayBatchState:
    status: str = "open"  # "open" | "complete"
    processed_message_ids: list[str] = field(default_factory=list)


@dataclass
class CuratorState:
    schema_version: int = SCHEMA_VERSION
    initialized_at: str | None = None
    last_successful_run: str | None = None
    processed_message_ids: set[str] = field(default_factory=set)
    processed_files: dict[str, dict[str, Any]] = field(default_factory=dict)
    daily_batches: dict[str, DayBatchState] = field(default_factory=dict)
    # Einheit (chat:<erste Nachrichten-ID> | doc:<state_key>) -> {count, last_error, at}.
    # Wiederholt scheiternde Einheiten ruecken ans Ende der Warteschlange,
    # statt jedes Budget zu verbrauchen (Review 2026-09-29).
    failures: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Nachrichten-ID -> hoechste kuratierte Fassung (.revN); Grundlage der
    # Abdeckung je Chat (chatgpt_source.load_new_messages).
    processed_revisions: dict[str, int] = field(default_factory=dict)
    # Per retract zurueckgenommene Nachrichten: nie (wieder) kuratieren,
    # auch nicht als neue Fassung.
    retracted_ids: set[str] = field(default_factory=set)
    # Nachrichten-ID -> Pruefsummen der Absaetze, die TATSAECHLICH an das
    # Modell gingen. Grundlage der Abdeckung: absorbierte Nachrichten tragen
    # nichts bei, zurueckgenommene/neu eingestellte fallen heraus (Review
    # Runde 4, F1). Fehlt der Eintrag (aeltere Laeufe), gilt die ganze Fassung
    # bis processed_revisions als gesendet.
    fed_paragraphs: dict[str, list[str]] = field(default_factory=dict)
    # Einmalige Festschreibung von processed_revisions fuer aeltere Laeufe
    # (vorher Rueckfall ueber die wandernde Zeitgrenze last_successful_run, F2).
    coverage_migrated: bool = False
    # Grosse Dokumente in Teilen (seit 2026-10-07): state_key -> {hash, done,
    # parts}. Erst nach dem letzten Teil wird die Datei archiviert und unter
    # processed_files vermerkt.
    document_parts: dict[str, dict[str, Any]] = field(default_factory=dict)

    def is_message_processed(self, message_id: str) -> bool:
        return message_id in self.processed_message_ids

    def mark_messages_processed(self, day: str, message_ids: list[str]) -> None:
        self.processed_message_ids.update(message_ids)
        batch = self.daily_batches.setdefault(day, DayBatchState())
        for mid in message_ids:
            if mid not in batch.processed_message_ids:
                batch.processed_message_ids.append(mid)

    def mark_day_complete_if_no_gap(self, day: str) -> None:
        # Informativer Marker fuer die Statusanzeige. Kein hartes Gate:
        # neue Messages desselben Tages werden trotzdem beim naechsten
        # Lauf als Nachtrag erkannt (siehe Aufgabenstellung Abschnitt 19).
        batch = self.daily_batches.setdefault(day, DayBatchState())
        batch.status = "complete"

    def file_signature(self, key: str) -> dict[str, Any] | None:
        return self.processed_files.get(key)

    def mark_file_processed(self, key: str, signature: dict[str, Any]) -> None:
        self.processed_files[key] = signature


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state(project: Path) -> CuratorState:
    path = layout.state_path(project)
    if not path.is_file():
        return CuratorState()

    import json

    raw = json.loads(path.read_text(encoding="utf-8"))
    state = CuratorState(
        schema_version=raw.get("schema_version", SCHEMA_VERSION),
        initialized_at=raw.get("initialized_at"),
        last_successful_run=raw.get("last_successful_run"),
        processed_message_ids=set(raw.get("processed_message_ids", [])),
        processed_files=raw.get("processed_files", {}),
        failures=raw.get("failures", {}) or {},
        processed_revisions=raw.get("processed_revisions", {}) or {},
        retracted_ids=set(raw.get("retracted_ids", []) or []),
        fed_paragraphs=raw.get("fed_paragraphs", {}) or {},
        coverage_migrated=bool(raw.get("coverage_migrated", False)),
        document_parts=raw.get("document_parts", {}) or {},
    )
    for day, entry in (raw.get("daily_batches") or {}).items():
        state.daily_batches[day] = DayBatchState(
            status=entry.get("status", "open"),
            processed_message_ids=list(entry.get("processed_message_ids", [])),
        )
    return state


def save_state(project: Path, state: CuratorState) -> None:
    layout.ensure_polaris_dir(project)
    if state.initialized_at is None:
        state.initialized_at = _now_iso()
    # last_successful_run setzt nur ein fehlerfreier Lauf (pipeline.record_last_run);
    # vorher bei jedem Speichern, auch beim Vermerken eines Fehlschlags.

    payload = {
        "schema_version": state.schema_version,
        "initialized_at": state.initialized_at,
        "last_successful_run": state.last_successful_run,
        "processed_message_ids": sorted(state.processed_message_ids),
        "processed_files": state.processed_files,
        "failures": state.failures,
        "processed_revisions": state.processed_revisions,
        "retracted_ids": sorted(state.retracted_ids),
        "fed_paragraphs": state.fed_paragraphs,
        "coverage_migrated": state.coverage_migrated,
        "document_parts": state.document_parts,
        "daily_batches": {
            day: {"status": b.status, "processed_message_ids": b.processed_message_ids}
            for day, b in sorted(state.daily_batches.items())
        },
    }
    storage.atomic_write_json(layout.state_path(project), payload)
