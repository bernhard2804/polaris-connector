"""Zentrale Pfad- und Default-Konfiguration der Polaris Bridge.

Alle Pfade werden dynamisch aus Path.home() abgeleitet, kein Benutzername
wird hart codiert.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import yaml

HOME = Path.home()

CONFIG_DIR = HOME / ".config" / "polaris"
CONFIG_FILE = CONFIG_DIR / "config.yaml"
PROJECTS_FILE = CONFIG_DIR / "projects.yaml"

DATA_DIR = HOME / ".local" / "share" / "polaris"
PENDING_DIR = DATA_DIR / "pending"
PROCESSED_DIR = DATA_DIR / "processed"
FAILED_DIR = DATA_DIR / "failed"
# Abgewehrte Vermischungen (seit 2026-09-29): der Curator sperrt beide
# beteiligten Projekte, solange hier eine Datei liegt.
CONFLICTS_DIR = DATA_DIR / "conflicts"
# Aus Chat-Abbildern erzeugte Turn-Dateien - bewusst NICHT im beobachteten
# Inbox-Ordner: jede Datei loeste dort die Path-Unit aus, und viele Abbilder
# auf einmal legten Service und Path-Unit per Start-Limit still (2026-09-29).
EXPANDED_DIR = DATA_DIR / "expanded"
LOCK_FILE = DATA_DIR / "bridge.lock"

STATE_DIR = HOME / ".local" / "state" / "polaris"
LOG_FILE = STATE_DIR / "polaris.log"

# Browser-Downloads landen hier (Unterordner "polaris-inbox" relativ zum
# Standard-Downloadverzeichnis des Browsers). Fuer dieses System wurde
# ~/Downloads als Standard-Downloadverzeichnis ermittelt (xdg-user-dir
# DOWNLOAD).
INBOX_DIR = HOME / "Downloads" / "polaris-inbox"

# Vorschlag fuer den ersten Start: existiert "~/Projekte", wird es als
# project_root in config.yaml eingetragen. Auf dem Entwicklungsrechner ist das ein rclone-
# Mount des Google-Drive-Ordners "Projekte" (rclone-projekte.service); jeder
# andere lokale oder eingehaengte Ordner funktioniert genauso und wird mit
# `polaris roots add <Ordner>` eingetragen.
DEFAULT_PROJECT_ROOT_CANDIDATES = [HOME / "Projekte"]


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, DATA_DIR, PENDING_DIR, PROCESSED_DIR, FAILED_DIR, CONFLICTS_DIR, EXPANDED_DIR, STATE_DIR, INBOX_DIR):
        d.mkdir(parents=True, exist_ok=True)


@dataclass
class Config:
    project_roots: list[str] = field(default_factory=list)
    # Vorschlag im Extension-Popup fuer noch nicht zugeordnete Claude-Projekte
    # (Ordnername unter einem project_root); wird nie automatisch angewendet.
    suggested_folder_for_claude: str | None = None
    # Weitere Schluessel anderer Bausteine (z. B. memory_mirror, seit
    # 2026-10-08) bleiben beim Speichern unveraendert erhalten.
    extra: dict = field(default_factory=dict)

    @property
    def project_root_paths(self) -> list[Path]:
        return [Path(p).expanduser() for p in self.project_roots]


def load_config() -> Config:
    ensure_dirs()
    if not CONFIG_FILE.exists():
        detected = [str(p) for p in DEFAULT_PROJECT_ROOT_CANDIDATES if p.is_dir()]
        cfg = Config(project_roots=detected)
        save_config(cfg)
        return cfg

    with CONFIG_FILE.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    known = {"project_roots", "suggested_folder_for_claude"}
    return Config(
        project_roots=raw.get("project_roots") or [],
        suggested_folder_for_claude=raw.get("suggested_folder_for_claude"),
        extra={k: v for k, v in raw.items() if k not in known},
    )


def save_config(cfg: Config) -> None:
    ensure_dirs()
    payload = {"project_roots": cfg.project_roots}
    if cfg.suggested_folder_for_claude:
        payload["suggested_folder_for_claude"] = cfg.suggested_folder_for_claude
    payload.update(cfg.extra)
    from .storage import atomic_write_text

    atomic_write_text(CONFIG_FILE, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))


def add_project_root(path: str) -> Path:
    """Traegt einen bestehenden Ordner als project_root ein (absolut, ~
    aufgeloest). ValueError bei fehlendem Ordner oder Doppeleintrag."""
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"{root} ist kein vorhandener Ordner")
    cfg = load_config()
    if any(p.resolve() == root for p in cfg.project_root_paths):
        raise ValueError(f"{root} ist bereits eingetragen")
    cfg.project_roots.append(str(root))
    save_config(cfg)
    return root


def remove_project_root(path: str) -> Path:
    """Entfernt einen project_root-Eintrag (Vergleich nach Aufloesung von ~).
    Prueft keine Zuordnungen - das macht der Aufrufer."""
    target = Path(path).expanduser()
    cfg = load_config()
    keep = [r for r in cfg.project_roots if Path(r).expanduser() != target
            and Path(r).expanduser().resolve() != target.resolve()]
    if len(keep) == len(cfg.project_roots):
        raise ValueError(f"{target} ist nicht eingetragen")
    cfg.project_roots = keep
    save_config(cfg)
    return target
