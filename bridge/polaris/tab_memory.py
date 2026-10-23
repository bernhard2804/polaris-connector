"""Speicherwaechter fuer Firefox-Tabs (seit 2026-10-23, Extension 0.4.4).

Anlass: Ein ChatGPT-Tab wuchs auf 20-26 GB (RAM + Swap); systemd-oomd beendete
daraufhin ganz Firefox mit allen Tabs (22.10. und 23.10.). Firefox gibt
Erweiterungen keinen Zugriff auf den Speicher eines Tabs. Der Native Host ist
aber ein Kindprozess des Firefox-Hauptprozesses und kann dessen Tab-Prozesse
in /proc lesen. Welcher Prozess zu welcher Seite gehoert, steht dort nicht;
gewarnt wird deshalb fuer den groessten Tab-Prozess.

Gezaehlt wird VmRSS + VmSwap: beim Vorfall lag der Grossteil im Swap.

Die Extension fragt einmal pro Minute nach, solange ein ChatGPT- oder
Claude-Tab offen ist.
Der Host zeigt selbst eine Desktop-Meldung (notify-send) und merkt sich in
STATE_DIR/tab-memory.json, wofuer er schon gewarnt hat.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from . import config

GIB = 1024 ** 3
WARN_BYTES = 4 * GIB
CRITICAL_BYTES = 8 * GIB
# Bleibt ein Tab kritisch, wird nach dieser Zeit erneut gewarnt.
CRITICAL_REPEAT_SECONDS = 10 * 60
LEVELS = ("ok", "warn", "critical")

PROC = Path("/proc")


def _status_bytes(pid: int) -> int | None:
    """VmRSS + VmSwap eines Prozesses in Bytes; None, wenn nicht lesbar."""
    try:
        text = (PROC / str(pid) / "status").read_text(encoding="utf-8")
    except OSError:
        return None
    total = 0
    for line in text.splitlines():
        if line.startswith(("VmRSS:", "VmSwap:")):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                total += int(parts[1]) * 1024
    return total


def _ppid(pid: int) -> int | None:
    try:
        stat = (PROC / str(pid) / "stat").read_text(encoding="utf-8")
    except OSError:
        return None
    # Feld 2 (comm) steht in Klammern und darf Leerzeichen enthalten.
    rest = stat[stat.rfind(")") + 2:].split()
    return int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else None


def _is_tab_process(pid: int) -> bool:
    try:
        raw = (PROC / str(pid) / "cmdline").read_bytes()
    except OSError:
        return False
    # Firefox schreibt seine Argumente als eine Zeichenkette mit Leerzeichen
    # zurueck, nicht NUL-getrennt.
    args = raw.replace(b"\0", b" ").split()
    return b"-contentproc" in args and args[-1] == b"tab"


def tab_processes(browser_pid: int) -> list[dict]:
    """Tab-Prozesse eines Firefox-Hauptprozesses, groesster zuerst.

    Firefox startet sie ueber einen Forkserver (Kind des Hauptprozesses);
    gesucht wird deshalb unter Kindern und Enkeln."""
    parents = {}
    for entry in PROC.iterdir():
        if entry.name.isdigit():
            parents[int(entry.name)] = _ppid(int(entry.name))
    owners = {browser_pid} | {pid for pid, ppid in parents.items() if ppid == browser_pid}
    found = []
    for pid, ppid in parents.items():
        if ppid not in owners or not _is_tab_process(pid):
            continue
        size = _status_bytes(pid)
        if size is not None:
            found.append({"pid": pid, "bytes": size})
    found.sort(key=lambda p: p["bytes"], reverse=True)
    return found


def level_for(size: int) -> str:
    if size >= CRITICAL_BYTES:
        return "critical"
    if size >= WARN_BYTES:
        return "warn"
    return "ok"


def should_notify(state: dict, pid: int, level: str, now: float) -> bool:
    """Neu warnen bei hoeherer Stufe, anderem Prozess oder anhaltend kritisch."""
    if level == "ok":
        return False
    if state.get("pid") != pid or LEVELS.index(level) > LEVELS.index(state.get("level", "ok")):
        return True
    return level == "critical" and now - float(state.get("at", 0)) >= CRITICAL_REPEAT_SECONDS


def _gb(size: int) -> str:
    return f"{size / GIB:.1f}".replace(".", ",")


def _notify(level: str, size: int) -> bool:
    if os.environ.get("POLARIS_NO_NOTIFY") or not shutil.which("notify-send"):
        return False
    if level == "critical":
        title = f"Firefox-Tab belegt {_gb(size)} GB – bitte neu laden"
        urgency = "critical"
    else:
        title = f"Firefox-Tab belegt {_gb(size)} GB"
        urgency = "normal"
    body = ("Meist ist es ein ChatGPT- oder Claude-Tab. Dort F5 drücken (Neu laden) gibt den Speicher frei. "
            "Wächst er weiter, beendet das System sonst ganz Firefox.")
    try:
        subprocess.run(["notify-send", "-u", urgency, "-a", "Polaris", title, body],
                       check=False, timeout=10)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def check(browser_pid: int | None = None, now: float | None = None) -> dict:
    """Misst, bewertet und warnt bei Bedarf; Antwort fuer die Extension."""
    browser_pid = os.getppid() if browser_pid is None else browser_pid
    now = time.time() if now is None else now
    procs = tab_processes(browser_pid)
    largest = procs[0] if procs else None
    level = level_for(largest["bytes"]) if largest else "ok"
    state_path = config.STATE_DIR / "tab-memory.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    notified = False
    if largest and should_notify(state, largest["pid"], level, now):
        notified = _notify(level, largest["bytes"])
        state = {"pid": largest["pid"], "level": level, "at": now}
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state), encoding="utf-8")
    elif level == "ok" and state:
        state_path.unlink(missing_ok=True)
    return {
        "level": level,
        "largest": largest,
        "warn_bytes": WARN_BYTES,
        "critical_bytes": CRITICAL_BYTES,
        "notified": notified,
    }
