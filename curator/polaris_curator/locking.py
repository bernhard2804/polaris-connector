"""Projektsperre fuer alle schreibenden Curator-Befehle (seit 2026-09-29).

Frueher sperrte nur der Kurationslauf. Verdichtung (compact), Neueinstufung
(reclassify --apply) und Ruecknahme (retract) lasen knowledge.md, riefen ggf.
minutenlang das Modell auf und schrieben dann eine Fassung auf Basis des
alten Texts - ein dazwischen fertig gewordener Lauf ging verloren, obwohl
seine Nachrichten im State schon als verarbeitet galten (Review 2026-09-29).

Regel: lange Arbeit (Modellaufruf) ausserhalb der Sperre; unter der Sperre
erneut lesen und nur schreiben, wenn sich der Text nicht geaendert hat.
"""

from __future__ import annotations

import contextlib
import fcntl
from pathlib import Path
from typing import Iterator

from . import layout


@contextlib.contextmanager
def project_lock(project: Path) -> Iterator[bool]:
    """Nicht blockierend; liefert False, wenn ein anderer Befehl laeuft."""
    layout.ensure_polaris_dir(project)
    with layout.lock_path(project).open("w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
