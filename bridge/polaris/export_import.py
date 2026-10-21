"""Import des offiziellen ChatGPT-Datenexports (seit 2026-09-25).

ChatGPT -> Einstellungen -> Datenkontrollen -> Daten exportieren liefert ein
ZIP mit conversations.json (bzw. mehreren conversations*.json). Unterhaltungen
aus ChatGPT-Projekten tragen gizmo_id="g-p-<id>" (neuere Exporte stattdessen
conversation_template_id) - dieselbe ID, unter der die
Extension sie archiviert. Zweck: Luecken schliessen, z. B. die Zeit vom
14.09. bis 25.09.2026, in der die Extension nach einer ChatGPT-Aenderung
nichts archivierte, oder Unterhaltungen, die nie im Browser geoeffnet wurden.

Regeln:
- nur der aktuelle Gespraechsstrang (current_node -> Wurzel), keine
  verworfenen Verzweigungen; nur user/assistant mit sichtbarem Text
- bereits vorhandene Message-IDs werden uebersprungen (sonst legte
  storage.write_message wegen leicht abweichender Formatierung Revisionen an)
- aufeinanderfolgende Assistenten-Nachrichten (Werkzeug-Zwischenschritte,
  mehrteilige Antworten) werden wie in der Extension zu EINEM Turn mit der
  ID der ersten Nachricht zusammengefasst (seit 2026-09-29) - sonst kaemen
  fuer bereits archivierte Antworten zusaetzliche Teil-Nachrichten dazu
- laeuft unter der Pipeline-Sperre und mit derselben Konfliktpruefung wie
  die Inbox (eine ID, die schon in einem anderen Projekt/Chat liegt, wird
  nicht importiert)
- captured_at = Erstellzeit der Nachricht, damit der Curator nach echten
  Tagen batcht
- Projekte ohne Mapping werden nur gemeldet (polaris map ...), nicht geraten
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from . import config, mapping, snapshot, storage



def iter_conversations(source: Path) -> Iterator[dict]:
    def load(data: bytes):
        payload = json.loads(data.decode("utf-8"))
        yield from payload if isinstance(payload, list) else [payload]

    if source.is_file() and zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as zf:
            names = sorted(n for n in zf.namelist()
                           if Path(n).name.startswith("conversations") and n.endswith(".json"))
            for name in names:
                yield from load(zf.read(name))
    elif source.is_dir():
        for p in sorted(source.glob("conversations*.json")):
            yield from load(p.read_bytes())
    elif source.is_file():
        yield from load(source.read_bytes())
    else:
        raise FileNotFoundError(source)


def events_from_conversation(conv: dict, captured_fallback: str) -> list[dict]:
    """Seit 2026-09-29 dieselbe Turn-Bildung wie fuer Chat-Abbilder der
    Extension (snapshot.py) - der Datenexport hat dasselbe Format."""
    # Exporte seit spaetestens 2026-09-30 (conversations-000.json ...) haben
    # kein gizmo_id mehr; die Projekt-ID steht in conversation_template_id.
    # Ohne diesen Rueckfall meldete der Import 0 Projekt-Unterhaltungen.
    if not conv.get("gizmo_id") and str(conv.get("conversation_template_id") or "").startswith("g-p-"):
        conv = {**conv, "gizmo_id": conv["conversation_template_id"]}
    events = snapshot.events_from_conversation(conv, captured_fallback, capture="api")
    for e in events:
        e["import"] = {"source": "chatgpt-export"}
    return events


@dataclass
class ImportReport:
    imported: dict[str, int] = field(default_factory=dict)
    existing: int = 0
    skipped_before_since: int = 0
    unmapped: dict[str, int] = field(default_factory=dict)
    conversations: int = 0
    conflicts: int = 0


class ImportBusyError(RuntimeError):
    pass


def import_export(source: Path, since: datetime | None = None, dry_run: bool = False) -> ImportReport:
    from .pipeline import pipeline_lock  # spaet: pipeline richtet beim Import Logging ein

    with pipeline_lock() as acquired:
        if not acquired:
            raise ImportBusyError("Bridge verarbeitet gerade - Import bitte gleich wiederholen")
        return _import_export(source, since, dry_run)


def _import_export(source: Path, since: datetime | None, dry_run: bool) -> ImportReport:
    config.ensure_dirs()
    cfg = config.load_config()
    report = ImportReport()
    now = datetime.now(timezone.utc).isoformat()
    for conv in iter_conversations(source):
        events = events_from_conversation(conv, now)
        if not events:
            continue
        report.conversations += 1
        project_id = events[0]["project"]["id"]
        store = mapping.load_store()
        entry = store.get(project_id)
        local_path = Path(entry.local_path) if entry and entry.status == "ok" else None
        if local_path is None or not local_path.is_dir():
            report.unmapped[project_id] = report.unmapped.get(project_id, 0) + len(events)
            continue
        for event in events:
            created = event["message"]["source_timestamp"]
            if since and created and datetime.fromisoformat(created) < since:
                report.skipped_before_since += 1
                continue
            # Wie in der Pipeline: unter der schon bekannten ID pruefen (ein per DOM
            # archivierter Turn fuehrt evtl. eine andere seiner Nachrichten als ID;
            # Review Runde 3, H2).
            storage.canonicalize(local_path, event)
            target = storage.messages_dir(local_path) / f"{storage.sanitize_id(event['message']['id'])}.json"
            if target.exists():
                report.existing += 1
                continue
            known = [Path(m.local_path) for m in store.projects.values() if m.local_path]
            clash = storage.find_conflicting_message(local_path, event, known)
            if clash:
                # Der Export ordnet die Nachricht verbindlich einem Chat zu - ist
                # sie schon woanders archiviert, ist das ein Beleg fuer eine
                # Vermischung. Wie in der Pipeline ablegen, damit der Curator
                # beide Projekte sperrt (Review Runde 2, 2026-09-29).
                report.conflicts += 1
                if not dry_run:
                    from .pipeline import record_conflict

                    record_conflict(event, clash, local_path, name=f"import__{storage.sanitize_id(event['message']['id'])}.json")
                continue
            if not dry_run:
                storage.write_message(local_path, event)
                storage.update_conversation_metadata(local_path, event, event["message"]["id"])
            report.imported[local_path.name] = report.imported.get(local_path.name, 0) + 1
    return report
