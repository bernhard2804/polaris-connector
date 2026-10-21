"""Automatische Zuordnung ChatGPT-Projektname -> lokaler Projektordner.

Reihenfolge (siehe README): exakter Name -> normalisierter Name -> Fuzzy
Match bei sehr hoher Sicherheit -> sonst kein Match (Pending).

Keine semantische/LLM-basierte Zuordnung.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

FUZZY_THRESHOLD = 0.87
FUZZY_MIN_GAP = 0.12

_NUMBERING_PREFIX = re.compile(r"^\s*\d+[_\-.\s]+")


def normalize_name(name: str, strip_numbering: bool = True) -> str:
    name = unicodedata.normalize("NFKC", name)
    name = name.strip().casefold()
    if strip_numbering:
        name = _NUMBERING_PREFIX.sub("", name)
    name = name.replace("_", " ").replace("-", " ")
    name = re.sub(r"\s+", " ", name).strip()
    return name


def list_candidate_dirs(project_roots: list[Path]) -> list[Path]:
    candidates: list[Path] = []
    for root in project_roots:
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            if entry.is_dir() and not entry.name.startswith("."):
                candidates.append(entry)
    return candidates


@dataclass
class MatchResult:
    path: Path | None
    method: str  # "exact" | "exact-normalized" | "fuzzy" | "none"
    confidence: float


def match_project(chatgpt_name: str, project_roots: list[Path], strict: bool = False) -> MatchResult:
    """strict=True (automatische Zuordnung): Nummern-Praefixe zaehlen mit -
    "2 Kunde" ist nicht "Kunde" (Review Runde 2, 2026-09-29). Ohne strict
    nur fuer Vorschlaege."""
    candidates = list_candidate_dirs(project_roots)
    if not candidates:
        return MatchResult(None, "none", 0.0)

    # A: exakter Name (case-sensitive, nur Whitespace getrimmt)
    exact = [c for c in candidates if c.name.strip() == chatgpt_name.strip()]
    if len(exact) == 1:
        return MatchResult(exact[0], "exact", 1.0)

    # B: normalisierter exakter Name
    target_norm = normalize_name(chatgpt_name, strip_numbering=not strict)
    normalized_matches = [c for c in candidates if normalize_name(c.name, strip_numbering=not strict) == target_norm]
    if len(normalized_matches) == 1:
        return MatchResult(normalized_matches[0], "exact-normalized", 0.99)

    # C: Fuzzy Match, nur bei eindeutig hoher Sicherheit
    scored = []
    for c in candidates:
        ratio = difflib.SequenceMatcher(None, target_norm, normalize_name(c.name)).ratio()
        scored.append((ratio, c))
    scored.sort(key=lambda t: t[0], reverse=True)

    if scored:
        best_score, best_path = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else 0.0
        if best_score >= FUZZY_THRESHOLD and (best_score - second_score) >= FUZZY_MIN_GAP:
            return MatchResult(best_path, "fuzzy", best_score)

    return MatchResult(None, "none", 0.0)
