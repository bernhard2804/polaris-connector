"""Persistente ChatGPT-Projekt-ID -> lokaler Projektpfad Zuordnung.

Gespeichert in ~/.config/polaris/projects.yaml. Sobald eine Project ID
einmal erfolgreich gemappt wurde, ist dieses Mapping massgeblich -
unabhaengig davon, ob sich der ChatGPT-Projektname spaeter aendert.
"""

from __future__ import annotations

import contextlib
import fcntl

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

import yaml

from . import config, storage
from .matching import match_project

AUTO_MAP_METHODS = ("exact", "exact-normalized")


@dataclass
class ProjectMapping:
    project_id: str
    chatgpt_name: str
    local_path: str
    mapping_method: str
    confidence: float
    status: str = "ok"  # "ok" | "missing_target"


@dataclass
class ProjectStore:
    projects: dict[str, ProjectMapping] = field(default_factory=dict)

    def get(self, project_id: str) -> Optional[ProjectMapping]:
        return self.projects.get(project_id)

    def set(self, mapping: ProjectMapping) -> None:
        self.projects[mapping.project_id] = mapping


def load_store() -> ProjectStore:
    config.ensure_dirs()
    if not config.PROJECTS_FILE.exists():
        return ProjectStore()
    with config.PROJECTS_FILE.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    store = ProjectStore()
    for pid, entry in (raw.get("projects") or {}).items():
        store.set(
            ProjectMapping(
                project_id=pid,
                chatgpt_name=entry.get("chatgpt_name", ""),
                local_path=entry.get("local_path", ""),
                mapping_method=entry.get("mapping_method", "manual"),
                confidence=float(entry.get("confidence", 0.0)),
                status=entry.get("status", "ok"),
            )
        )
    return store


def save_store(store: ProjectStore) -> None:
    config.ensure_dirs()
    payload = {
        "projects": {
            pid: {
                "chatgpt_name": m.chatgpt_name,
                "local_path": m.local_path,
                "mapping_method": m.mapping_method,
                "confidence": m.confidence,
                "status": m.status,
            }
            for pid, m in store.projects.items()
        }
    }
    storage.atomic_write_text(config.PROJECTS_FILE, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))


@contextlib.contextmanager
def store_lock() -> Iterator[None]:
    """Sperre fuer Lesen-Aendern-Schreiben von projects.yaml. Pipeline,
    Native Host (Popup-Zuordnung) und CLI schrieben sonst gleichzeitig, und
    eine der beiden Aenderungen ging verloren (Review 2026-09-29). Blockierend
    - die Abschnitte sind kurz."""
    config.ensure_dirs()
    with (config.CONFIG_DIR / ".projects.lock").open("w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def is_path_safe(local_path: Path, project_roots: list[Path]) -> bool:
    """Verhindert, dass ein Mapping (versehentlich oder ueber einen
    manipulierten Symlink) ausserhalb der konfigurierten project_roots
    zeigt."""
    try:
        resolved = local_path.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    for root in project_roots:
        try:
            root_resolved = root.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        # Nur echte Unterordner, nie der Stammordner selbst (sonst landeten
        # Rohdaten direkt in ~/Projekte, neben allen Kundenordnern).
        if root_resolved in resolved.parents:
            return True
    return False


def resolve_project(
    project_id: str, project_name: str, cfg: config.Config
) -> tuple[Optional[Path], str, ProjectStore]:
    with store_lock():
        return _resolve_project(project_id, project_name, cfg)


def _resolve_project(
    project_id: str, project_name: str, cfg: config.Config
) -> tuple[Optional[Path], str, ProjectStore]:
    """Liefert (local_path oder None, status_message, store).

    status_message ist einer von: "mapped", "mapped-missing-target",
    "newly-mapped", "pending".
    """
    store = load_store()
    existing = store.get(project_id)

    if existing:
        local_path = Path(existing.local_path)
        target_ok = local_path.is_dir() and is_path_safe(local_path, cfg.project_root_paths)
        if existing.status == "missing_target":
            if not target_ok:
                return None, "mapped-missing-target", store
            # Ordner ist wieder da (typisch: rclone-Mount war kurz weg).
            # Frueher blieb die Zuordnung bis zu einem manuellen `polaris map`
            # gesperrt und alles lief nach pending (Review 2026-09-29).
            existing.status = "ok"
            store.set(existing)
            save_store(store)
            return local_path, "mapped", store
        if not target_ok:
            existing.status = "missing_target"
            store.set(existing)
            save_store(store)
            return None, "mapped-missing-target", store
        return local_path, "mapped", store

    result = match_project(project_name, cfg.project_root_paths, strict=True)
    # Automatisch nur bei (normalisiert) exakt gleichem Namen. Ein Fuzzy-
    # Treffer ("Schmidt Bau" -> "Schmitt Bau", "Kunde 2" -> "Kunde")
    # koennte Kundendaten ungefragt in den Ordner eines anderen Kunden legen;
    # er bleibt nur Vorschlag fuer die Zuordnung im Popup (Review 2026-09-29).
    if result.path is None or result.method not in AUTO_MAP_METHODS:
        return None, "pending", store

    if not is_path_safe(result.path, cfg.project_root_paths):
        return None, "pending", store

    mapping = ProjectMapping(
        project_id=project_id,
        chatgpt_name=project_name,
        local_path=str(result.path),
        mapping_method=result.method,
        confidence=result.confidence,
        status="ok",
    )
    store.set(mapping)
    save_store(store)
    return result.path, "newly-mapped", store


class NeedsConfirmation(Exception):
    """Zuordnung wuerde eine bestehende umhaengen oder einen Ordner teilen."""


def mapping_risks(project_id: str, local_path: Path) -> list[str]:
    """Gruende, warum eine Zuordnung bestaetigt werden muss (leer = unkritisch).
    Frueher hing ein Fehlklick im Popup eine bestehende Zuordnung still um
    oder legte zwei Kunden in einen Ordner (Review Runde 2, 2026-09-29).
    Mehrere Zuordnungen auf einen Ordner sind erlaubt (z. B. Firma:
    ChatGPT + zwei Claude-Projekte), aber nur bewusst."""
    store = load_store()
    risks: list[str] = []
    target = Path(local_path).resolve()
    existing = store.get(project_id)
    if existing and Path(existing.local_path).resolve() != target:
        risks.append(f"ist bisher {Path(existing.local_path).name} zugeordnet")
    others = [
        m for pid, m in store.projects.items()
        if pid != project_id and m.local_path and Path(m.local_path).resolve() == target
    ]
    if others:
        names = ", ".join(sorted({m.chatgpt_name or m.project_id for m in others}))
        risks.append(f"{target.name} gehoert schon zu: {names}")
    return risks


def manual_map(project_id: str, local_path: Path, chatgpt_name: str = "", confirm: bool = False) -> ProjectMapping:
    with store_lock():
        risks = mapping_risks(project_id, local_path)
        if risks and not confirm:
            raise NeedsConfirmation("; ".join(risks))
        return _manual_map(project_id, local_path, chatgpt_name)


def _manual_map(project_id: str, local_path: Path, chatgpt_name: str) -> ProjectMapping:
    store = load_store()
    existing = store.get(project_id)
    mapping = ProjectMapping(
        project_id=project_id,
        chatgpt_name=chatgpt_name or (existing.chatgpt_name if existing else ""),
        local_path=str(local_path),
        mapping_method="manual",
        confidence=1.0,
        status="ok",
    )
    store.set(mapping)
    save_store(store)
    return mapping


def unmap(project_id: str) -> bool:
    with store_lock():
        store = load_store()
        if project_id in store.projects:
            del store.projects[project_id]
            save_store(store)
            return True
        return False
