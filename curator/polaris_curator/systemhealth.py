"""Health-Check fuer die selbst gebauten Bausteine auf dem Entwicklungsrechner (seit 2026-09-25).

Speist das Omarchy-Bar-Widget (vorher reines Curator-Widget). Liest nur
Zustandsdateien, Metadaten und systemd-Zustaende - keine teuren Pruefungen
(z. B. kein `omarchy-backup status`, das jede gesicherte Datei hasht; die
Drift zeigt das Backup-Widget selbst). Jede Komponente liefert
{id, label, level: ok|warn|error, detail}; level "error" nur fuer Dinge, bei
denen tatsaechlich etwas verloren gehen oder ausfallen kann.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import health as archive_health
from . import integrity

HOME = Path.home()
EXTENSION_ID = "polaris-chatgpt-connector@mstarchy.local"
NATIVE_MANIFEST = HOME / ".mozilla" / "native-messaging-hosts" / "polaris_bridge.json"
FIREFOX_DIR = HOME / ".config" / "mozilla" / "firefox"
BACKUP_STATE = HOME / ".local" / "share" / "omarchy-backup" / "state.json"
POLARIS_CONFIG_DIR = HOME / ".config" / "polaris"
MEMLINT = HOME / ".config" / "ai-agents" / "tools" / "memlint.py"
MEMORY_MIRROR_STATE = HOME / ".local" / "state" / "polaris" / "memory-mirror.json"
MEMORY_MIRROR_MAX_DAYS = 2
# memlint-Befunde, bei denen Wissen verloren gehen oder nach aussen gelangen
# kann -> rot; alles andere (kaputter Verweis, fehlender Tag, Rueckkopie) gelb.
MEMLINT_SEVERE = {"backup", "entrypoints", "secrets"}
MIN_EXTENSION_VERSION = (0, 1, 2)
# Omarchy lehnt symlinkte Plugin-Ordner ab, aktiv ist eine Kopie (install.sh).
WIDGET_ID = "mst.polaris-curator"
WIDGET_SRC = Path(__file__).resolve().parents[2] / "omarchy-plugin" / WIDGET_ID
WIDGET_ACTIVE = HOME / ".config" / "omarchy" / "plugins" / WIDGET_ID
WIDGET_SYNC_HINT = "./install.sh --yes --no-units --no-backup --no-memory, dann omarchy restart shell"

_LEVELS = {"ok": 0, "warn": 1, "error": 2}


def _c(cid: str, label: str, level: str, detail: str) -> dict:
    return {"id": cid, "label": label, "level": level, "detail": detail}


def _systemctl_user(*args: str) -> str:
    try:
        return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _age_days(iso: str | None, now: datetime) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return (now - dt).total_seconds() / 86400


def _fmt_age(days: float | None) -> str:
    if days is None:
        return "nie"
    if days < 1 / 24:
        return "gerade eben"
    if days < 1:
        return f"vor {int(days * 24)} h"
    return f"vor {int(days)} Tag(en)"


def check_archive(projects: dict, max_days: int) -> dict:
    h = archive_health.collect(projects, max_days)
    if h["warnings"]:
        level = "error" if any("keine ChatGPT-Nachricht" in w for w in h["warnings"]) else "warn"
        return _c("chatgpt-archive", "ChatGPT-Archivierung", level, " ".join(h["warnings"]))
    last = h["last_chatgpt_message_at"]
    if last is None:
        return _c("chatgpt-archive", "ChatGPT-Archivierung", "ok", "noch keine ChatGPT-Nachricht archiviert")
    days = _age_days(last, datetime.now(timezone.utc))
    return _c("chatgpt-archive", "ChatGPT-Archivierung", "ok", f"letzte Nachricht {_fmt_age(days)}")


def check_claude_archive(projects: dict, max_days: int, now: datetime) -> dict:
    """Eigene Zeile fuer Claude (seit Extension 0.2.0): laengere Frist als bei
    ChatGPT und hoechstens gelb, weil Claude unregelmaessiger genutzt wird;
    neutral, solange noch nie eine Claude-Nachricht ankam."""
    latest = None
    count = 0
    # Mehrere Zuordnungen (ChatGPT + Claude-Projekte) koennen auf denselben
    # Ordner zeigen - Ordner zaehlen, nicht Zuordnungen.
    for path in sorted({p.resolve() for p in projects.values()}):
        d = path / "raw" / "claude" / "messages"
        m = archive_health._latest_mtime(d)
        if m is not None:
            count += 1
            latest = m if latest is None or m > latest else latest
    if latest is None:
        return _c("claude-archive", "Claude-Archivierung", "ok", "noch keine Claude-Nachricht archiviert")
    days = (now.timestamp() - latest) / 86400
    detail = f"letzte Nachricht {_fmt_age(days)} ({count} Projekt(e) mit Claude-Quelle)"
    if days > max_days:
        return _c("claude-archive", "Claude-Archivierung", "warn",
                  f"seit {int(days)} Tagen keine Claude-Nachricht – falls du Claude genutzt hast: "
                  "Erweiterung/Berechtigung für claude.ai und Sync im Popup prüfen")
    return _c("claude-archive", "Claude-Archivierung", "ok", detail)


def check_journals(projects: dict) -> dict:
    """Unterbrochene Kurationsschritte (journal.py): offen laenger als 1 h oder
    beschaedigt -> rot, das Projekt wird dann nicht (vollstaendig) kuratiert."""
    from . import journal

    label = "Kurationsschritte"
    stuck = []
    now = datetime.now(timezone.utc).timestamp()
    for path in sorted({p.resolve() for p in projects.values()}):
        jp = journal.existing_path(path)
        if jp is None:
            continue
        try:
            journal.pending(path)
            age = now - jp.stat().st_mtime
            if age > 3600:
                stuck.append(f"{path.name} (offen seit {int(age // 3600)} h)")
        except (journal.JournalConflict, OSError) as exc:
            stuck.append(f"{path.name} ({exc})")
    if stuck:
        return _c("journal", label, "error",
                  "unterbrochen: " + ", ".join(stuck) + " – `polaris-curator journal <Projekt>`")
    return _c("journal", label, "ok", "keiner offen")


PENDING_STALE_DAYS = 7


def check_curation(projects: dict, now: datetime | None = None) -> dict:
    """Ergebnis der letzten Laeufe je Projekt (.polaris/last-run.json, seit
    2026-09-29): gelb bei Fehlern, wichtigen Warnungen (Dokument bleibt
    liegen/gekuerzt, Kontextgrenze, nicht zugeordnete Nachrichten),
    wiederholt scheiternden Einheiten oder Rueckstand aelter als 7 Tage.
    Vorher meldete das Widget nur den Exit-Code des Timer-Dienstes."""
    from datetime import date

    from . import state as state_mod

    label = "Kuration"
    today = (now or datetime.now(timezone.utc)).astimezone().date()
    problems: list[str] = []
    for path in sorted({p.resolve() for p in projects.values()}):
        lr = path / ".polaris" / "last-run.json"
        try:
            rec = json.loads(lr.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rec = None
        if rec:
            if rec.get("aborted_reason") == "integrity":
                pass  # meldet schon die Zeile "Projekttrennung"
            elif rec.get("errors") or rec.get("aborted_reason"):
                first = (rec.get("errors") or [rec.get("aborted_reason")])[0]
                problems.append(f"{path.name}: letzter Lauf mit Fehler ({str(first)[:90]})")
            for w in rec.get("warnings") or []:
                problems.append(f"{path.name}: {w[:110]}")
            oldest = rec.get("oldest_pending_day")
            if oldest:
                try:
                    age = (today - date.fromisoformat(oldest)).days
                except ValueError:
                    age = 0
                if age > PENDING_STALE_DAYS:
                    problems.append(f"{path.name}: Rückstand seit {age} Tagen ({rec.get('pending_chat_batches')} Tages-Batches)")
        try:
            from . import config as config_mod, context as context_mod

            pcfg = config_mod.load_config(path)
            full = getattr(pcfg, "full_context_chars", 0)
            cap = getattr(pcfg, "index_cap", context_mod.INDEX_CAP)
            for fname, is_dec in (("knowledge.md", False), ("open-loops.md", False), ("decisions.md", True)):
                fp = path / fname
                if full and fp.is_file():
                    share = context_mod.index_share(fp.read_text(encoding="utf-8"), full, is_dec, cap)
                    if share > 0.5:
                        problems.append(f"{path.name}/{fname}: Verzeichnis belegt {int(share * 100)} % des "
                                        "Kontext-Budgets – aufräumen (compact / review-loops) oder full_context_chars erhöhen")
        except Exception:  # noqa: BLE001 - nur Anzeige
            pass
        try:
            st = state_mod.load_state(path)
            repeated = [k for k, f in (st.failures or {}).items() if (f or {}).get("count", 0) >= 2]
        except Exception:  # noqa: BLE001 - nur Anzeige
            repeated = []
        if repeated:
            problems.append(f"{path.name}: {len(repeated)} Einheit(en) scheitern wiederholt")
    if problems:
        uniq = list(dict.fromkeys(problems))
        return _c("curation", label, "warn", " · ".join(uniq[:3]) + (f" (+{len(uniq) - 3})" if len(uniq) > 3 else ""))
    return _c("curation", label, "ok", "keine Auffälligkeiten")


def check_separation(mappings: dict | None = None) -> dict:
    """Projekttrennung (seit 2026-09-29, Vorfall zweier vermischter Kundenprojekte): rot, wenn im
    Rohbestand Nachrichten liegen, die zu einem anderen Projekt/Chat gehoeren
    (der Curator laesst betroffene Projekte dann aus); gelb, wenn die Bridge
    einen solchen Versuch abgewehrt hat oder ein Chat-Verzeichnis fremde IDs
    fuehrt."""
    label = "Projekttrennung"
    try:
        findings = integrity.scan(mappings)
    except OSError as exc:
        return _c("separation", label, "warn", f"Prüfung fehlgeschlagen: {exc}")
    blocking = [f for f in findings if f.blocking]
    if blocking:
        projects = sorted({p for f in blocking for p in f.projects})
        return _c("separation", label, "error",
                  f"{len(blocking)} Nachricht(en) im falschen Projekt/Chat ({', '.join(projects)}) – "
                  f"betroffene Projekte werden nicht kuratiert; z. B. {blocking[0].summary()}")
    notes = []
    for kind, text in (
        ("index_mismatch", "Chat-Verzeichnis(se) mit fremden IDs"),
        ("unreadable", "unlesbare Nachrichtendatei(en)"),
        ("unknown_project", "Nachricht(en) ohne zugeordnete Projekt-ID (werden nicht kuratiert)"),
    ):
        hits = [f for f in findings if f.kind == kind]
        if hits:
            notes.append(f"{len(hits)} {text} ({', '.join(sorted({p for f in hits for p in f.projects}))})")
    if notes:
        return _c("separation", label, "warn", "; ".join(notes))
    return _c("separation", label, "ok", "keine Nachricht im falschen Projekt")


def _firefox_profiles() -> list[Path]:
    return [p for p in FIREFOX_DIR.glob("*") if (p / "extensions.json").is_file()]


def _version_tuple(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.split(".")[:3])
    except ValueError:
        return (0,)


def check_extension() -> dict:
    for profile in _firefox_profiles():
        try:
            addons = json.loads((profile / "extensions.json").read_text(encoding="utf-8")).get("addons", [])
        except (OSError, ValueError):
            continue
        for a in addons:
            if a.get("id") != EXTENSION_ID:
                continue
            version = a.get("version") or "?"
            origins = list((a.get("userPermissions") or {}).get("origins") or [])
            # Nachtraeglich erteilte (optionale) Seitenzugriffe speichert Firefox
            # nicht in extensions.json, sondern in extension-preferences.json.
            try:
                prefs = json.loads((profile / "extension-preferences.json").read_text(encoding="utf-8"))
                origins += (prefs.get(EXTENSION_ID) or {}).get("origins") or []
            except (OSError, ValueError):
                pass
            if a.get("userDisabled") or a.get("appDisabled"):
                return _c("extension", "Firefox-Erweiterung", "error", f"v{version} ist deaktiviert")
            if not a.get("active", True) and not a.get("visible", True):
                # Von einer temporaer (about:debugging) geladenen Version mit
                # derselben ID ueberlagert; deren Rechte stehen nicht hier.
                return _c("extension", "Firefox-Erweiterung", "warn",
                          f"läuft als temporär geladene Version (installiert: v{version}) – fällt nach "
                          "einem Firefox-Neustart weg; neue Version signieren lassen und installieren")
            if not any("chatgpt.com" in o for o in origins):
                return _c("extension", "Firefox-Erweiterung", "error",
                          f"v{version}: Zugriff auf chatgpt.com nicht erteilt – exportiert nur nach Klick "
                          "(about:addons → Polaris → Berechtigungen)")
            if _version_tuple(version) < MIN_EXTENSION_VERSION:
                return _c("extension", "Firefox-Erweiterung", "warn",
                          f"installiert ist v{version}; eine neuere Version läuft höchstens temporär "
                          "und fällt nach einem Firefox-Neustart weg (signieren lassen)")
            if _version_tuple(version) >= (0, 2, 0) and not any("claude.ai" in o for o in origins):
                return _c("extension", "Firefox-Erweiterung", "warn",
                          f"v{version}: Zugriff auf claude.ai nicht erteilt – Claude-Export ruht "
                          "(Polaris-Symbol auf claude.ai anklicken → Zugriff erlauben)")
            granted = "chatgpt.com" + (" und claude.ai" if any("claude.ai" in o for o in origins) else "")
            return _c("extension", "Firefox-Erweiterung", "ok", f"v{version}, Zugriff auf {granted} erteilt")
    return _c("extension", "Firefox-Erweiterung", "error", "nicht installiert")


def check_native_host() -> dict:
    try:
        manifest = json.loads(NATIVE_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _c("native-host", "Native Host", "warn",
                  "nicht registriert – Erweiterung nutzt Downloads (bridge/install-native-host.sh)")
    path = Path(manifest.get("path", ""))
    if not (path.is_file() and os.access(path, os.X_OK)):
        return _c("native-host", "Native Host", "warn", f"registriert, aber {path} fehlt/nicht ausführbar")
    return _c("native-host", "Native Host", "ok", "registriert")


def _tree(root: Path) -> dict[str, bytes]:
    return {str(f.relative_to(root)): f.read_bytes() for f in sorted(root.rglob("*")) if f.is_file()}


def check_widget(src: Path | None = None, active: Path | None = None) -> dict | None:
    """Ist die aktive Widget-Kopie auf dem Stand des Programmordners? Ohne
    installiertes Widget (--no-widget) keine Zeile. Liest nur ein paar kleine
    Dateien."""
    src = src or WIDGET_SRC
    active = active or WIDGET_ACTIVE
    if not active.is_dir() or not src.is_dir():
        return None
    try:
        want, have = _tree(src), _tree(active)
    except OSError as exc:
        return _c("widget", "Widget", "warn", f"Kopie nicht lesbar: {exc}")
    differ = sorted(n for n in want.keys() | have.keys() if want.get(n) != have.get(n))
    if differ:
        names = ", ".join(differ[:3]) + (" …" if len(differ) > 3 else "")
        return _c("widget", "Widget", "warn", f"Kopie veraltet ({names}) – {WIDGET_SYNC_HINT}")
    return _c("widget", "Widget", "ok", "Kopie aktuell")


UPDATE_STATE = HOME / ".local" / "state" / "polaris" / "update-check.json"
UPDATE_SCRIPT = Path(__file__).resolve().parents[2] / "update.sh"
UPDATE_MAX_AGE_H = 24


def check_update(now: datetime) -> dict | None:
    """Gibt es einen neueren Polaris-Stand (git-Checkout, seit 2026-10-23)?
    Ausnahme vom Grundsatz "nur Zustandsdateien": hoechstens einmal am Tag ein
    `git fetch` ueber `update.sh --check`, sonst nur die Zustandsdatei. Ohne
    git-Checkout (Installationspaket) keine Zeile."""
    if not (UPDATE_SCRIPT.parent / ".git").exists():
        return None
    state: dict = {}
    try:
        state = json.loads(UPDATE_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    age = _age_days(state.get("checked_at"), now)
    if age is None or age * 24 >= UPDATE_MAX_AGE_H:
        try:
            subprocess.run([str(UPDATE_SCRIPT), "--check"], capture_output=True, timeout=70, check=False)
            state = json.loads(UPDATE_STATE.read_text(encoding="utf-8"))
        except (OSError, ValueError, subprocess.SubprocessError):
            return _c("update", "Update", "ok", "Prüfung nicht möglich (offline?) – polaris update --check")
    if int(state.get("behind") or 0) > 0:
        return _c("update", "Update", "warn",
                  f"verfügbar: {state.get('local', '?')} → {state.get('remote', '?')} – polaris update")
    return _c("update", "Update", "ok", f"aktuell ({state.get('local', '?')})")


def check_curator_timer() -> dict:
    result = _systemctl_user("show", "polaris-curator.service", "--property=Result", "--value")
    enabled = _systemctl_user("is-enabled", "polaris-curator.timer")
    if enabled != "enabled":
        return _c("curator", "Curator", "error", "Timer nicht aktiviert")
    if result and result != "success":
        return _c("curator", "Curator", "warn", f"letzter Timer-Lauf: {result}")
    return _c("curator", "Curator", "ok", "Timer aktiv, letzter Lauf erfolgreich")


def check_llm() -> dict:
    """Sind die CLIs der konfigurierten Modell-Runtime da? Nur `which`, kein
    Aufruf (kostet nichts); Anmeldung prueft `polaris-curator llm-check --call`."""
    from . import config as config_mod, llm

    try:
        cfg = config_mod.load_config()
    except Exception as e:  # noqa: BLE001 - kaputte curator.yaml
        return _c("llm", "Modell", "error", f"curator.yaml unlesbar: {e}")
    if cfg.runtime not in llm.RUNTIMES:
        return _c("llm", "Modell", "error", f"unbekannte runtime {cfg.runtime!r}")
    bins = llm.runtime_binaries(cfg)
    missing = [b for b, path in bins.items() if path is None]
    main = llm.main_binary(cfg)
    if main in missing:
        return _c("llm", "Modell", "error", f"{main} fehlt – polaris-curator llm-check")
    if missing:
        return _c("llm", "Modell", "warn", f"{cfg.runtime} {cfg.model}; {', '.join(missing)} (Ersatz/Prüfer) fehlt")
    return _c("llm", "Modell", "ok", f"{cfg.runtime} {cfg.model}")


def check_backup(now: datetime) -> dict:
    if not BACKUP_STATE.exists():
        # Optionaler Baustein (seit 2026-10-07): auf Rechnern ohne
        # omarchy-backup kein Fehler, sondern ein klarer Hinweis.
        if shutil.which("omarchy-backup") is None:
            return _c("backup", "omarchy-backup", "ok", "nicht eingerichtet (optional)")
        return _c("backup", "omarchy-backup", "warn", "installiert, aber noch kein Snapshot")
    try:
        state = json.loads(BACKUP_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _c("backup", "omarchy-backup", "error", "Zustandsdatei unlesbar")
    snaps = state.get("snapshots") or []
    last = max((s.get("created_at", "") for s in snaps), default="")
    pushed = max((s.get("created_at", "") for s in snaps if s.get("remote_pushed")), default="")
    last_days, pushed_days = _age_days(last or None, now), _age_days(pushed or None, now)
    doctor = state.get("last_doctor_status") or "?"
    result = _systemctl_user("show", "omarchy-backup-snapshot.service", "--property=Result", "--value")
    detail = f"Snapshot {_fmt_age(last_days)}, hochgeladen {_fmt_age(pushed_days)}, doctor {doctor}"
    if doctor not in ("HEALTHY", "?"):
        return _c("backup", "omarchy-backup", "error", detail)
    if pushed_days is None or pushed_days > 7:
        return _c("backup", "omarchy-backup", "error", detail)
    if (last_days or 99) > 2 or (pushed_days or 99) > 2 or (result and result != "success"):
        suffix = f", letzter Lauf: {result}" if result and result != "success" else ""
        return _c("backup", "omarchy-backup", "warn", detail + suffix)
    return _c("backup", "omarchy-backup", "ok", detail)


RCLONE_VFS_META = HOME / ".cache" / "rclone" / "vfsMeta"
UPLOAD_STALE_SECONDS = 3600


def _upload_backlog() -> tuple[int, int] | None:
    """(Anzahl, Alter der aeltesten in h) lokal geaenderter, noch nicht
    hochgeladener Dateien (rclone --vfs-cache-mode full, Dirty=true) aelter als
    1 h. Schreiben gelingt lokal auch ohne Upload - ein Upload-Stau war sonst
    unsichtbar (Review Runde 3)."""
    if not RCLONE_VFS_META.is_dir():
        return None
    now = datetime.now().timestamp()
    stale = []
    for meta in RCLONE_VFS_META.rglob("*"):
        try:
            if not meta.is_file() or now - meta.stat().st_mtime < UPLOAD_STALE_SECONDS:
                continue
            if b'"Dirty": true' in meta.read_bytes()[:2000]:
                stale.append(now - meta.stat().st_mtime)
        except OSError:
            continue
    if not stale:
        return None
    return len(stale), int(max(stale) // 3600)


def _fstype(path: Path) -> str:
    try:
        return subprocess.run(["findmnt", "-n", "-o", "FSTYPE", "--target", str(path)],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _read_polaris_yaml(name: str) -> dict:
    try:
        import yaml

        return yaml.safe_load((POLARIS_CONFIG_DIR / name).read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - fehlend/kaputt: wie leer behandeln
        return {}


def check_mount() -> dict:
    """Projektordner-Speicher (seit 2026-10-07 nicht mehr fest ~/Projekte als
    Google-Drive-Mount): prueft jeden project_root aus der Bridge-Konfiguration.
    Fehler, wenn ein Root fehlt oder zugeordnete Projektordner darunter fehlen
    (so faellt auch ein abgefallener Mount auf, dessen leerer Mountpunkt noch
    existiert). Bei rclone-Mounts zusaetzlich der Upload-Stau."""
    roots = [Path(str(r)).expanduser() for r in (_read_polaris_yaml("config.yaml").get("project_roots") or [])]
    if not roots:
        return _c("mount", "Projektordner", "warn", "keine project_roots konfiguriert – polaris roots add <Ordner>")
    label = "Projektordner (" + ", ".join(str(r).replace(str(HOME), "~", 1) for r in roots) + ")"
    mapped = [Path(str(e.get("local_path"))).expanduser()
              for e in (_read_polaris_yaml("projects.yaml").get("projects") or {}).values()
              if isinstance(e, dict) and e.get("status") == "ok" and e.get("local_path")]
    kinds = []
    for root in roots:
        short = str(root).replace(str(HOME), "~", 1)
        fstype = _fstype(root) if root.is_dir() else ""
        rclone = fstype.startswith("fuse.rclone")
        hint = "Laufwerk eingehängt?"
        if not root.is_dir():
            return _c("mount", label, "error", f"{short} fehlt – {hint}")
        missing = [m for m in mapped if m.parent == root and not m.is_dir()]
        if missing:
            return _c("mount", label, "error",
                      f"{len(missing)} zugeordnete(r) Projektordner unter {short} fehlt – {hint}")
        if rclone:
            pending = _upload_backlog()
            if pending:
                n, oldest_h = pending
                level = "error" if oldest_h >= 24 else "warn"
                return _c("mount", label, level,
                          f"eingehängt, aber {n} Datei(en) seit {oldest_h} h nicht hochgeladen – rclone-Log prüfen")
            kinds.append("rclone eingehängt")
        else:
            kinds.append("erreichbar")
    return _c("mount", label, "ok", ", ".join(dict.fromkeys(kinds)))


def check_memory() -> dict:
    """Maschinengedaechtnis ~/.config/ai-agents per memlint (deterministisch, stdlib)."""
    label = "Maschinengedächtnis"
    if not MEMLINT.is_file():
        if not MEMLINT.parent.parent.is_dir():
            return _c("memory", label, "ok", "nicht eingerichtet (optional)")
        return _c("memory", label, "warn", f"{MEMLINT} fehlt")
    try:
        proc = subprocess.run(["python3", str(MEMLINT), "--json"], capture_output=True, text=True, timeout=30,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        report = json.loads(proc.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return _c("memory", label, "warn", "memlint konnte nicht ausgeführt werden")
    findings = report.get("findings") or []
    errors = [f for f in findings if f.get("level") == "error"]
    warnings = [f for f in findings if f.get("level") != "error"]
    if errors:
        first = errors[0]
        level = "error" if any(f.get("check") in MEMLINT_SEVERE for f in errors) else "warn"
        return _c("memory", label, level,
                  f"{len(errors)} Fehler (z. B. {first.get('check')}: {first.get('file')}) – memlint.py ausführen")
    # Groessenhinweise halten die Handoffs schlank, sind aber kein Ausfall.
    note = f", {len(warnings)} Hinweis(e)" if warnings else ""
    detail = f"konsistent, {report.get('stats', {}).get('tags', 0)} Einträge{note}"
    level, mirror = mirror_status()
    return _c("memory", label, level, detail + (f", {mirror}" if mirror else ""))


def _polaris_config_value(key: str):
    return _read_polaris_yaml("config.yaml").get(key)


def mirror_status(now: datetime | None = None) -> tuple[str, str]:
    """Spiegel des Gedaechtnisses (memory-mirror, seit 2026-10-08). Nur gelb:
    Die lokale Fassung ist die Arbeitskopie, ein fehlender Spiegel legt nichts lahm."""
    if not _polaris_config_value("memory_mirror"):
        return "ok", ""
    now = now or datetime.now(timezone.utc)
    try:
        state = json.loads(MEMORY_MIRROR_STATE.read_text())
    except (OSError, ValueError):
        return "warn", "Spiegel noch nie gelaufen – polaris-memory-mirror"
    if state.get("last_result") == "mirror_newer":
        return "warn", "Spiegel neuer als lokal – erst zurückholen (polaris-memory-mirror)"
    days = _age_days(state.get("last_success"), now)
    if days is None or days > MEMORY_MIRROR_MAX_DAYS:
        why = f" ({state.get('detail')})" if state.get("last_result") != "ok" and state.get("detail") else ""
        return "warn", f"Spiegel {_fmt_age(days)}{why}"
    return "ok", f"gespiegelt {_fmt_age(days)}"


# Typische Platzfresser, die sich gefahrlos verkleinern lassen (Idee aus
# omasweep, 2026-10-08). Nur gemessen und genannt, nie geloescht. Nicht dabei:
# ~/.cache/rclone (VFS-Cache von ~/Projekte, kann noch nicht hochgeladene
# Dateien enthalten).
RECLAIM_CANDIDATES = [
    ("pacman-Cache", Path("/var/cache/pacman/pkg"), "paccache -rk2"),
    ("Papierkorb", HOME / ".local" / "share" / "Trash", ""),
    ("yay-Cache", HOME / ".cache" / "yay", ""),
    ("paru-Cache", HOME / ".cache" / "paru", ""),
    ("Journal", Path("/var/log/journal"), "journalctl --vacuum-time=4weeks"),
    ("Coredumps", Path("/var/lib/systemd/coredump"), ""),
]
RECLAIM_MIN_BYTES = 512 * 2**20
COREDUMP_DIR = Path("/var/lib/systemd/coredump")
CRASH_WINDOW_HOURS = 24


def _du_bytes(path: Path) -> int | None:
    """Groesse per du; Teilfehler (z. B. root-eigene Unterordner im yay-Cache)
    liefern trotzdem die lesbare Summe."""
    try:
        out = subprocess.run(["du", "-sb", "--one-file-system", str(path)],
                             capture_output=True, text=True, timeout=30).stdout
        return int(out.split()[0])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def _fmt_size(n: int) -> str:
    gb = n / 2**30
    return f"{gb:.1f} GB".replace(".", ",") if gb < 10 else f"{gb:.0f} GB"


MOUNTINFO = Path("/proc/self/mountinfo")


def _backing_device(path: str | Path) -> str | None:
    """Quellgeraet des Mounts, auf dem path liegt. Nicht st_dev: auf Btrfs hat
    jedes Subvolume (@, @pkg, @log ...) ein eigenes st_dev, teilt aber den Platz."""
    try:
        target = os.path.realpath(path)
        lines = MOUNTINFO.read_text().splitlines()
    except OSError:
        return None
    best, source = "", None
    for line in lines:
        pre, sep, post = line.partition(" - ")
        fields, tail = pre.split(), post.split()
        if not sep or len(fields) < 5 or len(tail) < 2:
            continue
        mnt = fields[4].replace("\\040", " ")
        if (target == mnt or target.startswith(mnt.rstrip("/") + "/")) and len(mnt) >= len(best):
            best, source = mnt, tail[1]
    return source


def reclaimable(mount: str) -> list[tuple[str, int, str]]:
    """Groesste Kandidaten auf demselben Geraet wie mount, absteigend."""
    dev = _backing_device(mount)
    if not dev:
        return []
    found = []
    for label, path, hint in RECLAIM_CANDIDATES:
        try:
            if not path.is_dir() or path.is_symlink() or _backing_device(path) != dev:
                continue
        except OSError:
            continue
        size = _du_bytes(path)
        if size and size >= RECLAIM_MIN_BYTES:
            found.append((label, size, hint))
    return sorted(found, key=lambda x: -x[1])


def _parse_coredump_name(name: str) -> tuple[str, datetime] | None:
    """core.<comm>.<uid>.<boot-id>.<pid>.<usec>[.zst|.lz4|.xz]; comm kann Punkte enthalten."""
    if not name.startswith("core."):
        return None
    base = name[5:]
    for ext in (".zst", ".lz4", ".xz"):
        if base.endswith(ext):
            base = base[: -len(ext)]
            break
    parts = base.rsplit(".", 4)
    if len(parts) != 5 or not parts[4].isdigit():
        return None
    comm = parts[0].replace("\\x20", " ")
    return comm, datetime.fromtimestamp(int(parts[4]) / 1e6, timezone.utc)


def check_crashes(now: datetime | None = None, window_hours: int = CRASH_WINDOW_HOURS) -> dict | None:
    """Neue Coredumps (Idee aus omasweep): gelb, solange ein Absturz juenger als
    window_hours ist. Liest nur die Dateinamen, keine Inhalte."""
    now = now or datetime.now(timezone.utc)
    try:
        names = [e.name for e in os.scandir(COREDUMP_DIR)]
    except OSError:
        return None
    dumps = [d for d in map(_parse_coredump_name, names) if d]
    recent = [comm for comm, ts in dumps if (now - ts).total_seconds() < window_hours * 3600]
    if recent:
        counts: dict[str, int] = {}
        for comm in recent:
            counts[comm] = counts.get(comm, 0) + 1
        top = sorted(counts.items(), key=lambda kv: -kv[1])
        listed = ", ".join(f"{c} ({n}×)" if n > 1 else c for c, n in top[:3])
        more = " u. a." if len(top) > 3 else ""
        return _c("crashes", "Abstürze", "warn",
                  f"{len(recent)} seit {window_hours} h: {listed}{more} – coredumpctl list")
    if dumps:
        last = max(dumps, key=lambda d: d[1])
        return _c("crashes", "Abstürze", "ok", f"keine neuen (zuletzt {last[0]}, {_fmt_age(_age_days(last[1].isoformat(), now))})")
    return _c("crashes", "Abstürze", "ok", "keine")


def check_system(now: datetime | None = None) -> list[dict]:
    items = []
    seen_devs: set[str | None] = set()
    for mount in ("/", "/home"):
        try:
            usage = shutil.disk_usage(mount)
        except OSError:
            continue
        pct = usage.used * 100 / usage.total
        level = "error" if pct > 95 else "warn" if pct > 85 else "ok"
        detail = f"{pct:.0f} % belegt, {usage.free / 2**30:.0f} GB frei"
        dev = _backing_device(mount)
        if level != "ok" and dev not in seen_devs:
            big = reclaimable(mount)[:4]
            if big:
                detail += " – größte Posten: " + ", ".join(
                    f"{label} {_fmt_size(size)}" + (f" ({hint})" if hint else "") for label, size, hint in big)
        seen_devs.add(dev)
        items.append(_c(f"disk{mount}", f"Platte {mount}", level, detail))
    failed = []
    for scope in (["systemctl"], ["systemctl", "--user"]):
        try:
            out = subprocess.run([*scope, "--failed", "--no-legend", "--plain"], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        failed += [line.split()[0] for line in out.splitlines() if line.strip()]
    items.append(_c("units", "systemd-Units", "warn" if failed else "ok",
                    ("fehlgeschlagen: " + ", ".join(failed[:5])) if failed else "keine fehlgeschlagen"))
    for bat in sorted(Path("/sys/class/power_supply").glob("BAT*")):
        try:
            cap = int((bat / "capacity").read_text().strip())
            status = (bat / "status").read_text().strip()
        except (OSError, ValueError):
            continue
        items.append(_c("battery", "Akku", "warn" if cap < 15 and status == "Discharging" else "ok", f"{cap} % ({status})"))
        break
    crashes = check_crashes(now)
    if crashes:
        items.append(crashes)
    return items


# Gruppen fuer die Anzeige (seit 2026-09-29, Nutzerwunsch: weniger Zeilen).
# (id, Anzeigename, [(Kurzname, Teil-ID)], Teil-ID fuer die OK-Zusammenfassung)
GROUPS = [
    ("capture", "Erfassung", [("ChatGPT", "chatgpt-archive"), ("Claude", "claude-archive"),
                              ("Erweiterung", "extension"), ("Native Host", "native-host")], "chatgpt-archive"),
    ("separation", "Projekttrennung", [("Projekttrennung", "separation")], "separation"),
    ("curator", "Curator", [("Timer", "curator"), ("Kurationsschritte", "journal"), ("Kuration", "curation"),
                                         ("Modell", "llm"), ("Widget", "widget")],
     "curator"),
    ("storage", "Speicher & Sicherung", [("Projektordner", "mount"), ("Backup", "backup")], "backup"),
    ("memory", "Maschinengedächtnis", [("Maschinengedächtnis", "memory")], "memory"),
]


def _group(gid: str, label: str, members: list[tuple[str, dict]], primary: dict | None) -> dict:
    """Eine Zeile je Gruppe: schlechtester Zustand der Teile; im Text nur die
    auffaelligen Teile, sonst eine kurze Zusammenfassung. Die Teile bleiben in
    "parts" (polaris-curator health --all)."""
    worst = max((_LEVELS[c["level"]] for _n, c in members), default=0)
    level = {0: "ok", 1: "warn", 2: "error"}[worst]
    if level == "ok":
        names = ", ".join(n for n, _c in members)
        if len(members) == 1:
            detail = members[0][1]["detail"]
        else:
            detail = f"{names} in Ordnung" + (f" – {primary['detail']}" if primary else "")
    else:
        bad = sorted(((n, c) for n, c in members if c["level"] != "ok"), key=lambda nc: -_LEVELS[nc[1]["level"]])
        prefix = len(members) > 1
        detail = " · ".join((f"{n}: " if prefix else "") + c["detail"] for n, c in bad)
    return {"id": gid, "label": label, "level": level, "detail": detail, "parts": [c for _n, c in members]}


def group_components(checks: list[dict]) -> list[dict]:
    by_id = {c["id"]: c for c in checks}
    used: set[str] = set()
    out = []
    for gid, label, parts, primary_id in GROUPS:
        members = [(name, by_id[pid]) for name, pid in parts if pid in by_id]
        if not members:
            continue
        used.update(pid for _n, pid in parts)
        out.append(_group(gid, label, members, by_id.get(primary_id)))
    # Unbekannte (neu hinzugekommene) Pruefungen nie verschlucken.
    out.extend(c for c in checks if c["id"] not in used)
    return out


def collect(projects: dict, max_days_without_chatgpt: int, max_days_without_claude: int = 7) -> dict:
    now = datetime.now(timezone.utc)
    checks = [
        check_archive(projects, max_days_without_chatgpt),
        check_claude_archive(projects, max_days_without_claude, now),
        check_separation(),
        check_journals(projects),
        check_curation(projects, now),
        check_extension(),
        check_native_host(),
        check_curator_timer(),
        check_llm(),
        check_backup(now),
        check_mount(),
        check_memory(),
        check_widget(),
        check_update(now),
    ]
    checks = [c for c in checks if c is not None]
    system = check_system(now)
    worst = max((_LEVELS[c["level"]] for c in checks + system), default=0)
    return {
        "generated_at": now.isoformat(),
        "level": {0: "ok", 1: "warn", 2: "error"}[worst],
        "components": group_components(checks),
        "checks": checks,
        "system": system,
    }
