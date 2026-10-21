"""Erkennt stille Ausfaelle der ChatGPT-Archivierung.

Anlass (2026-09-25): Die Firefox-Extension erkannte nach einer ChatGPT-
Aenderung 11 Tage lang kein Projekt; nichts wurde archiviert, und nichts hat
es gemeldet. Einzelne Projekte duerfen ruhen - kommt aber in KEINEM Projekt
mehr eine ChatGPT-Nachricht an, ist fast sicher die Kette Extension ->
Native Host/Download -> Bridge unterbrochen. Zusaetzlich werden liegen-
gebliebene Inbox-Dateien, abgelehnte Events und Bridge-Fehler gemeldet.

Nutzt nur Dateimetadaten (mtime), keine Inhalte; kennt die Bridge-Pfade,
importiert die Bridge aber bewusst nicht (lose Kopplung wie in config.py).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import layout

HOME = Path.home()
BRIDGE_INBOX = HOME / "Downloads" / "polaris-inbox"
BRIDGE_PENDING = HOME / ".local" / "share" / "polaris" / "pending"
# Seit 0.4.0 der Hauptweg: aus Chat-Abbildern zerlegte Turns (bridge config.EXPANDED_DIR).
BRIDGE_EXPANDED = HOME / ".local" / "share" / "polaris" / "expanded"
BRIDGE_FAILED = HOME / ".local" / "share" / "polaris" / "failed"
BRIDGE_LOG = HOME / ".local" / "state" / "polaris" / "polaris.log"
# Register der Bridge fuer Dateien, die nur noch "retry" ergeben (seit 2026-09-29).
BRIDGE_RETRY = HOME / ".local" / "state" / "polaris" / "retry.json"
# Letzter Zustand der Schnittstellen-Erfassung der Extension (Native Host).
CAPTURE_STATUS = HOME / ".local" / "state" / "polaris" / "capture-status.json"
# Chats mit zurueckgehaltener letzter Antwort (Bridge, seit Review Runde 5).
HELD_BACK = HOME / ".local" / "state" / "polaris" / "held-back.json"
RETRY_STALE_AFTER = timedelta(hours=1)
NOTIFY_STAMP = HOME / ".local" / "state" / "polaris" / "curator-health-notified.json"

INBOX_STALE_AFTER = timedelta(hours=1)
FAILED_RECENT = timedelta(days=7)
LOG_ERRORS_RECENT = timedelta(hours=24)


def _latest_mtime(directory: Path) -> float | None:
    latest = None
    try:
        with os.scandir(directory) as it:
            for entry in it:
                if entry.name.endswith(".json") and entry.is_file():
                    m = entry.stat().st_mtime
                    latest = m if latest is None or m > latest else latest
    except OSError:
        return None
    return latest


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None


def _count_files(directory: Path, pattern: str, newer_than: float | None = None,
                 older_than: float | None = None) -> int:
    n = 0
    for p in directory.glob(pattern):
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if newer_than is not None and m < newer_than:
            continue
        if older_than is not None and m > older_than:
            continue
        n += 1
    return n


_FAILED_LINE_RE = re.compile(r" ERROR failed file=(\S+) reason=(\S+)")
_CONFLICT_LINE_RE = re.compile(r" ERROR conflict file=(\S+)")
BRIDGE_CONFLICTS = HOME / ".local" / "share" / "polaris" / "conflicts"


def _recent_log_errors(since: datetime) -> int:
    """ERROR-Zeilen seit since. Zeilen zu abgelehnten Dateien zaehlen nur,
    solange die Datei noch in failed/ liegt - nach dem Aufraeumen soll der
    Health Check sofort wieder gruen sein (seit 2026-09-29)."""
    if not BRIDGE_LOG.is_file():
        return 0
    cutoff = since.strftime("%Y-%m-%d %H:%M:%S")
    n = 0
    try:
        with BRIDGE_LOG.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if " ERROR " not in line or line[:19] < cutoff:
                    continue
                m = _FAILED_LINE_RE.search(line)
                if m and not (BRIDGE_FAILED / m.group(1)).exists():
                    continue
                # Geklaerte Konflikte zaehlen nicht mehr (polaris conflicts --resolve).
                c = _CONFLICT_LINE_RE.search(line)
                if c and not (BRIDGE_CONFLICTS / c.group(1)).exists():
                    continue
                n += 1
    except OSError:
        return 0
    return n


def _held_back(now: datetime) -> list[str]:
    try:
        data = json.loads(HELD_BACK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for key, entry in (data or {}).items():
        try:
            since = datetime.fromisoformat(str(entry.get("since", "")).replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        if now - since > timedelta(hours=1):
            out.append(str(entry.get("title") or key))
    return out


CAPTURE_ERROR_FRESH = timedelta(hours=3)


def _capture_problem(now: datetime) -> str | None:
    """Warnung, wenn die ChatGPT-Schnittstelle zuletzt nur Fehler lieferte
    (die Extension liest dann aus der Seite - weniger robust)."""
    try:
        data = json.loads(CAPTURE_STATUS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    err = data.get("last_error") or {}
    try:
        err_at = datetime.fromisoformat(err.get("at", ""))
    except ValueError:
        return None
    last_ok = data.get("last_ok")
    try:
        ok_at = datetime.fromisoformat(last_ok) if last_ok else None
    except ValueError:
        ok_at = None
    # Nur ein frischer Fehler zaehlt (seit 2026-10-08, vorher 2 Tage): Jeder
    # weitere Abruf erneuert den Zeitstempel, ein echter Ausfall bleibt also
    # sichtbar, solange ChatGPT benutzt wird. Ein einzelner alter Fehler ohne
    # spaeteren Versuch (z. B. kurzer Netzaussetzer, danach nur unveraenderte
    # oder nicht zugeordnete Chats) loeste sonst noch Stunden spaeter eine
    # Desktop-Meldung aus.
    if (ok_at is None or ok_at < err_at) and now - err_at < CAPTURE_ERROR_FRESH:
        local = err_at.astimezone().strftime("%H:%M")
        return (f"ChatGPT-Schnittstelle um {local} nicht nutzbar ({err.get('error', '')[:100]}) - "
                "Erfassung liest ersatzweise aus der Seite.")
    return None


def _stuck_retries(now: datetime) -> list[tuple[str, str]]:
    try:
        data = json.loads(BRIDGE_RETRY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for name, entry in (data or {}).items():
        try:
            first = datetime.fromisoformat(entry.get("first", ""))
        except (ValueError, AttributeError):
            continue
        # Nur Dateien, die es noch gibt (Inbox oder Pending).
        if (not (BRIDGE_INBOX / name).exists() and not (BRIDGE_EXPANDED / name).exists()
                and not list(BRIDGE_PENDING.glob(f"*/{name}"))):
            continue
        if now - first > RETRY_STALE_AFTER:
            out.append((name, str(entry.get("last_error", ""))[:120]))
    return out


def collect(projects: dict[str, Path], max_days_without_chatgpt: int, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    per_project: dict[str, str | None] = {}
    latest = None
    for path in sorted({p.resolve() for p in projects.values()}):
        m = _latest_mtime(layout.messages_dir(path))
        per_project[path.name] = _iso(m)
        if m is not None and (latest is None or m > latest):
            latest = m

    warnings: list[str] = []
    days_since = None
    if projects:
        if latest is None:
            warnings.append("Noch keine ChatGPT-Nachricht archiviert.")
        else:
            days_since = (now.timestamp() - latest) / 86400
            if days_since > max_days_without_chatgpt:
                warnings.append(
                    f"Seit {int(days_since)} Tagen keine ChatGPT-Nachricht mehr archiviert "
                    f"(in keinem Projekt) - Firefox-Erweiterung, Berechtigung für chatgpt.com "
                    f"und Native Host prüfen."
                )

    inbox_stale = _count_files(BRIDGE_INBOX, "*.json", older_than=(now - INBOX_STALE_AFTER).timestamp()) + \
        _count_files(BRIDGE_EXPANDED, "*.json", older_than=(now - INBOX_STALE_AFTER).timestamp())
    pending = _count_files(BRIDGE_PENDING, "*/*.json")
    # Solange etwas in failed/ liegt, sichtbar - unabhaengig vom Alter (die
    # Datei behaelt die mtime aus der Inbox und war sonst evtl. sofort "zu alt";
    # Review Runde 3).
    failed_recent = _count_files(BRIDGE_FAILED, "*.json")
    log_errors = _recent_log_errors((now - LOG_ERRORS_RECENT).astimezone())
    if inbox_stale:
        warnings.append(f"{inbox_stale} Datei(en) liegen seit über einer Stunde unverarbeitet in der Bridge-Inbox.")
    if pending:
        warnings.append(f"{pending} ChatGPT-Nachricht(en) warten auf eine Projektzuordnung (polaris pending).")
    if failed_recent:
        warnings.append(f"{failed_recent} Datei(en) hat die Bridge abgelehnt (~/.local/share/polaris/failed/) - Grund im Bridge-Protokoll.")
    if log_errors:
        warnings.append(f"{log_errors} Fehler im Bridge-Protokoll in den letzten 24 Stunden.")
    held = _held_back(now)
    if held:
        names = ", ".join(f"„{t}“" for t in held[:3]) + (" …" if len(held) > 3 else "")
        warnings.append(
            f"{len(held)} Chat(s) mit noch nicht übernommener letzter Antwort seit über 1 h: {names} – "
            "Chat öffnen oder im Popup „Sync now“ (gelöschter Chat: `polaris held-back --clear …`)."
        )
    capture = _capture_problem(now)
    if capture:
        warnings.append(capture)
    stuck = _stuck_retries(now)
    if stuck:
        example = stuck[0]
        warnings.append(
            f"{len(stuck)} Datei(en) lassen sich seit über einer Stunde nicht ablegen "
            f"(z. B. {example[0]}: {example[1]}) - Projektordner/Mount prüfen."
        )

    return {
        "last_chatgpt_message_at": _iso(latest),
        "days_since_chatgpt_message": round(days_since, 1) if days_since is not None else None,
        "max_days_without_chatgpt": max_days_without_chatgpt,
        "projects_last_chatgpt_message_at": per_project,
        "bridge": {
            "inbox_stale": inbox_stale,
            "pending": pending,
            "failed_last_7_days": failed_recent,
            "log_errors_last_24h": log_errors,
        },
        "warnings": warnings,
    }


def notify_once_per_day(health: dict, now: datetime | None = None) -> bool:
    """Desktop-Benachrichtigung, hoechstens einmal pro Tag und Befund."""
    warnings = health.get("warnings") or []
    if os.environ.get("POLARIS_NO_NOTIFY") or not warnings or not shutil.which("notify-send"):
        return False
    now = now or datetime.now(timezone.utc)
    digest = hashlib.sha256("\n".join(warnings).encode()).hexdigest()[:16]
    today = now.astimezone().date().isoformat()
    try:
        stamp = json.loads(NOTIFY_STAMP.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stamp = {}
    if stamp.get("date") == today and stamp.get("digest") == digest:
        return False
    try:
        subprocess.run(
            ["notify-send", "-u", "critical", "-a", "Polaris Curator", "Polaris: Archivierung prüfen",
             "\n".join(warnings)],
            check=False, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    NOTIFY_STAMP.parent.mkdir(parents=True, exist_ok=True)
    NOTIFY_STAMP.write_text(json.dumps({"date": today, "digest": digest}), encoding="utf-8")
    return True
