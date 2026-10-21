"""Atomare Datei-Schreibvorgaenge. Bewusst eigenstaendig (keine Abhaengigkeit
von der Bridge), damit Curator und Bridge unabhaengig voneinander bleiben."""

from __future__ import annotations

import json
import os
from pathlib import Path


def _project_roots() -> list[Path]:
    """project_roots aus ~/.config/polaris/config.yaml (Bridge-Konfiguration)."""
    try:
        from . import config

        roots = config._read_yaml(config.GLOBAL_CONFIG_DIR / "config.yaml").get("project_roots") or []
        return [Path(str(r)).expanduser() for r in roots]
    except Exception:  # noqa: BLE001 - fehlende/kaputte Konfiguration: keine Pruefung
        return []


def ensure_dir(directory: Path) -> None:
    """Wie mkdir(parents=True), legt aber nie einen Projektordner selbst an.
    Ist ~/Projekte (rclone-Mount) weg, entstuenden sonst Schattenordner auf
    der lokalen Platte unter dem Mountpunkt: rclone verweigert danach das
    Einhaengen, und die Dateien waeren verdeckt (Architektur-Review
    2026-09-29; die Bridge prueft das schon seit Runde 2)."""
    for root in _project_roots():
        try:
            rel = directory.resolve().relative_to(root.resolve()) if root.exists() else directory.relative_to(root)
        except ValueError:
            continue
        if rel.parts:
            project_dir = root / rel.parts[0]
            if not project_dir.is_dir():
                raise OSError(f"Projektordner {project_dir} nicht erreichbar (Laufwerk eingehängt?)")
    directory.mkdir(parents=True, exist_ok=True)


def atomic_write_text(path: Path, content: str) -> None:
    ensure_dir(path.parent)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)
    # Zeitstempel ausdruecklich setzen: Auf dem rclone-Drive-Mount blieb nach
    # einem gescheiterten Upload (2026-10-04) die alte Aenderungszeit am
    # Objekt haengen, obwohl der Inhalt spaeter ersetzt wurde - eine knowledge.md
    # sah tagelang "seit 04.10. unveraendert" aus (2026-10-08).
    os.utime(path)


def atomic_write_json(path: Path, data: dict) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False))
