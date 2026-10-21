"""Explicit read-only tools scoped to one configured workspace."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

Tool = Callable[..., Awaitable[object]]


def _safe_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes the configured workspace: {relative!r}") from exc
    if candidate.is_symlink():
        raise ValueError(f"symbolic links are not readable in this PoC: {relative!r}")
    return candidate


def make_read_only_tools(workspace: Path) -> list[Tool]:
    root = workspace.resolve()
    if not root.is_dir():
        raise ValueError(f"workspace does not exist: {root}")

    async def list_project_files() -> list[str]:
        """List regular project files using paths relative to the configured workspace."""
        return sorted(
            str(path.relative_to(root))
            for path in root.rglob("*")
            if path.is_file() and not path.is_symlink()
        )

    async def search_project(pattern: str) -> list[str]:
        """Search project text files for a case-insensitive literal and return at most 50 matching lines."""
        if not pattern or len(pattern) > 200:
            raise ValueError("pattern must contain between 1 and 200 characters")
        needle = pattern.casefold()
        matches: list[str] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink() or path.stat().st_size > 1_000_000:
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(lines, start=1):
                if needle in line.casefold():
                    matches.append(f"{path.relative_to(root)}:{number}:{line[:500]}")
                    if len(matches) == 50:
                        return matches
        return matches

    async def read_project_file(path: str) -> str:
        """Read up to 16 KiB from one regular UTF-8 file inside the configured workspace."""
        candidate = _safe_path(root, path)
        if not candidate.is_file():
            raise ValueError(f"not a regular file: {path!r}")
        if candidate.stat().st_size > 16_384:
            raise ValueError(f"file exceeds the PoC read limit: {path!r}")
        return candidate.read_text(encoding="utf-8")

    return [list_project_files, search_project, read_project_file]
