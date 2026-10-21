"""Write-Ahead-Journal fuer einen Kurationsschritt (seit 2026-09-29).

Ein Schritt aendert bis zu drei Dateien (knowledge.md, decisions.md,
open-loops.md), danach den State und bei Dokumenten das Inbox-Archiv - lauter
einzelne Schreibvorgaenge. Bricht es dazwischen ab (Absturz, Drive-Mount weg,
Kill), war der Zustand halb: vorher kam es zu Dubletten; ein zwischenzeitlicher
Abgleich ueber die Herkunftsangaben machte halbe Schritte sogar dauerhaft
(Review Runde 2: Entscheidung verloren, weil knowledge.md schon geschrieben war).

Jetzt: vor dem ersten Schreiben wird der vollstaendige Zielzustand atomar
abgelegt (.polaris/journal.json): je Datei Hash des Ausgangstexts und der
neue Text, dazu die geplante State-Aenderung. Danach schreiben, State
speichern, Journal loeschen. Findet ein Lauf ein Journal vor, vollendet er den
Schritt - aber nur, wenn jede Datei noch den Ausgangs- oder schon den
Zieltext hat. Hat jemand sie inzwischen anders geaendert, wird nichts
angefasst und das Projekt bis zur Klaerung nicht kuratiert.

Ablage (seit 2026-10-10) lokal unter ~/.local/state/polaris/journals/, nicht
mehr in <Projekt>/.polaris/ auf dem rclone-Drive-Mount: Dort ging das Loeschen
des Journals verloren (Upload im Hintergrund lief noch), eine alte Fassung
tauchte wieder auf und sperrte ein Projekt zwei Tage lang, obwohl der Schritt laengst
erledigt war. Lokal ist das Journal ausserdem auch dann lesbar, wenn das
Laufwerk mitten im Schritt wegbricht. Ein altes Journal am frueheren Ort wird
weiter gelesen, vollendet und entfernt.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from . import layout, storage

NAME = "journal.json"


class JournalConflict(Exception):
    pass


def journal_dir() -> Path:
    # Zur Aufrufzeit ermittelt, damit Tests mit umgeleitetem HOME isoliert sind.
    return Path.home() / ".local" / "state" / "polaris" / "journals"


def path(project: Path) -> Path:
    """Ablageort fuer neue Journale: lokal, je Projekt eindeutig (Name zum
    Wiedererkennen, Hash des absoluten Pfads gegen gleichnamige Ordner)."""
    key = hashlib.sha256(str(Path(project).resolve()).encode("utf-8")).hexdigest()[:12]
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in Path(project).name)
    return journal_dir() / f"{safe}-{key}.json"


def legacy_path(project: Path) -> Path:
    """Frueherer Ort auf dem Drive-Mount (bis 2026-10-10)."""
    return layout.polaris_dir(project) / NAME


def existing_path(project: Path) -> Path | None:
    """Pfad eines offenen Journals (lokal vor altem Ort) oder None."""
    for p in (path(project), legacy_path(project)):
        if p.is_file():
            return p
    return None


def _sha(text: str | None) -> str | None:
    return None if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read(p: Path) -> str | None:
    return p.read_text(encoding="utf-8") if p.is_file() else None


def _checksum(record: dict) -> str:
    body = json.dumps({k: v for k, v in record.items() if k != "checksum"}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def begin(project: Path, files: dict[str, tuple[str | None, str]], state_update: dict[str, Any]) -> None:
    """files: {dateiname relativ zum Projekt: (Ausgangstext oder None, Zieltext)}.
    Die Pruefsumme erkennt ein abgeschnittenes Journal (auf dem Drive-Mount ist
    rename nicht zwingend atomar)."""
    record = {
        "files": {name: {"old_sha": _sha(old), "new_text": new} for name, (old, new) in files.items()},
        "state_update": state_update,
    }
    record["checksum"] = _checksum(record)
    d = journal_dir()
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)  # enthaelt den vollen Text der Wissensdateien
    storage.atomic_write_text(path(project), json.dumps(record, ensure_ascii=False))


def write_files(project: Path, files: dict[str, tuple[str | None, str]]) -> None:
    for name, (_old, new) in files.items():
        storage.atomic_write_text(project / name, new)


def finish(project: Path) -> None:
    for p in (path(project), legacy_path(project)):
        if p.exists():
            p.unlink()


def pending(project: Path) -> dict[str, Any] | None:
    """Offenes Journal oder None. Unlesbar/abgeschnitten -> JournalConflict
    (Projekt sperren und melden), nie ein Absturz des ganzen Laufs (Review
    Runde 3: brach sonst run-all fuer alle folgenden Projekte ab)."""
    p = existing_path(project)
    if p is None:
        return None
    try:
        record = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise JournalConflict(f"{p.name} unlesbar ({type(exc).__name__})") from exc
    if not isinstance(record, dict) or (record.get("checksum") and record["checksum"] != _checksum(record)):
        raise JournalConflict(f"{p.name} unvollstaendig (Pruefsumme)")
    return record


def ensure_none_pending(project: Path) -> None:
    """Fuer compact/reclassify/retract: bei offenem Schritt nichts aendern -
    sonst passte das Journal nicht mehr, und das Projekt hinge fest (Review
    Runde 3)."""
    try:
        record = pending(project)
    except JournalConflict as exc:
        raise JournalConflict(f"{exc} - erst klaeren (polaris-curator journal {project.name})") from exc
    if record is not None:
        raise JournalConflict(
            "ein unterbrochener Kurationsschritt ist offen - erst `polaris-curator run "
            f"{project.name}` ausfuehren (vollendet ihn), dann erneut"
        )


def replay_files(project: Path, record: dict[str, Any]) -> None:
    """Dateien auf den Zielstand bringen; JournalConflict, wenn eine Datei
    weder Ausgangs- noch Zieltext hat."""
    todo = []
    for name, spec in (record.get("files") or {}).items():
        current = _read(project / name)
        if current == spec["new_text"]:
            continue
        if _sha(current) != spec["old_sha"]:
            raise JournalConflict(f"{name} wurde seit dem abgebrochenen Schritt anders geaendert")
        todo.append((name, spec["new_text"]))
    for name, text in todo:
        storage.atomic_write_text(project / name, text)
