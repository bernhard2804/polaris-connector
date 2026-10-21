"""Gemeinsame Test-Hilfsfunktionen. Kein pytest noetig - reines unittest,
da auf diesem System kein pytest installiert ist und keine zusaetzliche
Abhaengigkeit fuer die Tests eingefuehrt werden soll."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

# polaris.config leitet seine Pfade beim Import aus Path.home() ab, und
# pipeline.py richtet dabei sein Logging ein. Ohne diese Umleitung landeten
# Testeintraege im echten ~/.local/state/polaris/polaris.log (2026-09-25).
# Muss vor dem ersten Import eines polaris-Moduls passieren.
import os as _os

_os.environ["HOME"] = tempfile.mkdtemp(prefix="polaris-test-home-")
# Keine echten Desktop-Benachrichtigungen aus Tests (pipeline._notify_conflict).
_os.environ["POLARIS_NO_NOTIFY"] = "1"

BRIDGE_ROOT = Path(__file__).resolve().parents[1] / "bridge"
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))


class TempHome:
    """Isoliert Tests komplett von der echten $HOME-Konfiguration, indem
    polaris.config auf ein temporaeres Verzeichnis umgebogen wird."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="polaris-test-"))

    def __enter__(self):
        from polaris import config

        self._orig = {
            "HOME": config.HOME,
            "CONFIG_DIR": config.CONFIG_DIR,
            "CONFIG_FILE": config.CONFIG_FILE,
            "PROJECTS_FILE": config.PROJECTS_FILE,
            "DATA_DIR": config.DATA_DIR,
            "PENDING_DIR": config.PENDING_DIR,
            "PROCESSED_DIR": config.PROCESSED_DIR,
            "FAILED_DIR": config.FAILED_DIR,
            "CONFLICTS_DIR": config.CONFLICTS_DIR,
            "EXPANDED_DIR": config.EXPANDED_DIR,
            "LOCK_FILE": config.LOCK_FILE,
            "STATE_DIR": config.STATE_DIR,
            "LOG_FILE": config.LOG_FILE,
            "INBOX_DIR": config.INBOX_DIR,
        }

        home = self.tmp
        config.HOME = home
        config.CONFIG_DIR = home / ".config" / "polaris"
        config.CONFIG_FILE = config.CONFIG_DIR / "config.yaml"
        config.PROJECTS_FILE = config.CONFIG_DIR / "projects.yaml"
        config.DATA_DIR = home / ".local" / "share" / "polaris"
        config.PENDING_DIR = config.DATA_DIR / "pending"
        config.PROCESSED_DIR = config.DATA_DIR / "processed"
        config.FAILED_DIR = config.DATA_DIR / "failed"
        config.CONFLICTS_DIR = config.DATA_DIR / "conflicts"
        config.EXPANDED_DIR = config.DATA_DIR / "expanded"
        config.LOCK_FILE = config.DATA_DIR / "bridge.lock"
        config.STATE_DIR = home / ".local" / "state" / "polaris"
        config.LOG_FILE = config.STATE_DIR / "polaris.log"
        config.INBOX_DIR = home / "Downloads" / "polaris-inbox"
        config.ensure_dirs()
        return self

    def __exit__(self, *exc):
        from polaris import config

        for k, v in self._orig.items():
            setattr(config, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_project_root(self, *project_names: str) -> Path:
        from polaris import config

        root = self.tmp / "Projekte"
        root.mkdir(parents=True, exist_ok=True)
        for name in project_names:
            (root / name).mkdir(parents=True, exist_ok=True)
        cfg = config.Config(project_roots=[str(root)])
        config.save_config(cfg)
        return root


def make_event(
    project_id="proj_abc",
    project_name="Musterfirma",
    conversation_id="conv_xyz",
    conversation_title="Test Chat",
    message_id="msg_1",
    parent_id=None,
    role="user",
    content="Hallo Welt",
    captured_at="2026-09-12T10:00:00Z",
) -> dict:
    return {
        "schema_version": 1,
        "source": "chatgpt",
        "project": {"id": project_id, "name": project_name},
        "conversation": {
            "id": conversation_id,
            "title": conversation_title,
            "url": f"https://chatgpt.com/c/{conversation_id}",
        },
        "message": {
            "id": message_id,
            "id_source": "native",
            "parent_id": parent_id,
            "parent_id_source": "dom-order" if parent_id else None,
            "role": role,
            "source_timestamp": None,
            "content": content,
        },
        "captured_at": captured_at,
    }
