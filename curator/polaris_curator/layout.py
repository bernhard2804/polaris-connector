"""Zentrale Pfad-Definitionen innerhalb eines Projektordners."""

from __future__ import annotations

from pathlib import Path

from . import storage


def knowledge_path(project: Path) -> Path:
    return project / "knowledge.md"


def decisions_path(project: Path) -> Path:
    return project / "decisions.md"


def open_loops_path(project: Path) -> Path:
    return project / "open-loops.md"


def polaris_dir(project: Path) -> Path:
    return project / ".polaris"


def state_path(project: Path) -> Path:
    return polaris_dir(project) / "curator-state.json"


def log_path(project: Path) -> Path:
    return polaris_dir(project) / "curator.log"


def lock_path(project: Path) -> Path:
    return polaris_dir(project) / "curator.lock"


CHAT_SOURCES = ("chatgpt", "claude")


def chat_messages_dirs(project: Path) -> list[tuple[str, Path]]:
    """Alle Chat-Quellen (seit 2026-09-25 auch Claude unter raw/claude/)."""
    return [(src, project / "raw" / src / "messages") for src in CHAT_SOURCES]


def messages_dir(project: Path) -> Path:
    return project / "raw" / "chatgpt" / "messages"


def conversations_dir(project: Path) -> Path:
    return project / "raw" / "chatgpt" / "conversations"


def inbox_dir(project: Path, kind: str) -> Path:
    assert kind in ("mail", "documents", "notes")
    return project / "inbox" / kind


def inbox_processed_dir(project: Path, kind: str) -> Path:
    return project / "inbox" / kind / "archive" / "processed"


def ensure_inbox_dirs(project: Path) -> None:
    for kind in ("mail", "documents", "notes"):
        storage.ensure_dir(inbox_dir(project, kind))


def ensure_polaris_dir(project: Path) -> None:
    storage.ensure_dir(polaris_dir(project))


def inbox_attachments_dir(project: Path, kind: str) -> Path:
    return project / "inbox" / kind / "archive" / "attachments"
