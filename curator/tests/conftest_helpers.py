from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

CURATOR_ROOT = Path(__file__).resolve().parents[1]
if str(CURATOR_ROOT) not in sys.path:
    sys.path.insert(0, str(CURATOR_ROOT))

# Tests duerfen keine echten Desktop-Benachrichtigungen ausloesen (health.py).
import os as _os  # noqa: E402

_os.environ["POLARIS_NO_NOTIFY"] = "1"

from polaris_curator import config as _config  # noqa: E402
from polaris_curator.config import CuratorConfig  # noqa: E402

# Nie die echten Projektzuordnungen lesen: sonst wuerde die
# Integritaetspruefung (integrity.py) in Tests das echte ~/Projekte scannen.
_config.PROJECTS_FILE = Path(tempfile.mkdtemp(prefix="polaris-curator-cfg-")) / "projects.yaml"
from polaris_curator import integrity as _integrity  # noqa: E402

_integrity.CONFLICTS_DIR = Path(tempfile.mkdtemp(prefix="polaris-curator-conflicts-"))
from polaris_curator import journal as _journal  # noqa: E402

# Journale liegen seit 2026-10-10 unter ~/.local/state; Tests nie dort
# schreiben lassen (vorher blieben Test-Journale im echten Verzeichnis liegen).
_JOURNAL_DIR = Path(tempfile.mkdtemp(prefix="polaris-curator-journals-"))
_journal.journal_dir = lambda: _JOURNAL_DIR


class TempProject:
    """Baut eine isolierte, temporaere Projektordner-Struktur (kein
    Zugriff auf das echte $HOME/Projekte)."""

    def __init__(self):
        self.path = Path(tempfile.mkdtemp(prefix="polaris-curator-test-"))

    def __enter__(self) -> Path:
        (self.path / "raw" / "chatgpt" / "messages").mkdir(parents=True)
        (self.path / "raw" / "chatgpt" / "conversations").mkdir(parents=True)
        (self.path / "inbox" / "mail").mkdir(parents=True)
        (self.path / "inbox" / "documents").mkdir(parents=True)
        (self.path / "inbox" / "notes").mkdir(parents=True)
        return self.path

    def __exit__(self, *exc):
        shutil.rmtree(self.path, ignore_errors=True)

    def write_message(
        self,
        message_id: str,
        conversation_id: str = "conv_1",
        role: str = "user",
        content: str = "Hallo Welt",
        captured_at: str = "2026-09-01T10:00:00.000Z",
        parent_id: str | None = None,
        conversation_title: str = "Test Chat",
    ) -> Path:
        event = {
            "schema_version": 1,
            "source": "chatgpt",
            "project": {"id": "proj_1", "name": "TestProjekt"},
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
        path = self.path / "raw" / "chatgpt" / "messages" / f"{message_id}.json"
        path.write_text(json.dumps(event), encoding="utf-8")
        return path


def fast_config(**overrides) -> CuratorConfig:
    cfg = CuratorConfig(
        model="fake-model",
        fallback_model="fake-fallback",
        max_batches_per_run=3,
        max_llm_calls_per_run=3,
        max_messages_per_batch=40,
        max_input_chars_per_batch=50000,
        max_total_input_chars_per_run=120000,
        max_curated_context_chars=50000,
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


PASSING_SELF_REVIEW = {
    "stakeholder_claims_marked_not_as_fact": True,
    "no_overgeneralization_from_single_case": True,
    "interventions_not_presented_as_proven": True,
    "systemic_implications_considered": True,
    "key_person_dependencies_considered": True,
    "customer_or_economic_impact_considered": True,
    "leadership_as_permanent_substitute_flagged": True,
    "entries_are_long_term_relevant_not_redundant": True,
    "claims_do_not_exceed_source_evidence": True,
    "notes": None,
}

EMPTY_CHANGE = {
    "knowledge_updates": [],
    "decisions_add": [],
    "decisions_update": [],
    "open_loops_add": [],
    "open_loops_close": [],
    "conflicts": [],
    "irrelevant_sources": [],
    "self_review": PASSING_SELF_REVIEW,
}

KNOWLEDGE_UPDATE_DEFAULTS = {
    "entry_type": "fact",
    "scope": "local",
    "evidence_strength": "single_source",
    "confidence": "medium",
    "mode": "append",
    "replaces_id": None,
    "source_message_ids": [],
    "related_existing_insight": None,
    "economic_relevance": None,
    "operating_model_relevance": None,
    "key_person_dependency": None,
    "causal_chain": None,
}

DECISION_ADD_DEFAULTS = {
    "decision_scope": "local",
    "validated": False,
    "validation_note": None,
    "source_message_ids": [],
}

DECISION_UPDATE_DEFAULTS = {
    "new_status": None,
    "replaced_by": None,
    "note": None,
    "validated": None,
    "validation_note": None,
    "source_message_ids": [],
}

OPEN_LOOP_ADD_DEFAULTS = {
    "loop_type": "other",
    "source_message_ids": [],
}

OPEN_LOOP_CLOSE_DEFAULTS = {
    "resolution_note": None,
    "source_message_ids": [],
}


def _with_defaults(items: list[dict], defaults: dict) -> list[dict]:
    return [{**defaults, **item} for item in items]


def make_change(**overrides) -> dict:
    """Baut ein valides Aenderungsobjekt fuer Tests. Fuellt fuer die neuen
    Pflichtfelder aus change_schema.py sinnvolle Defaults auf, damit
    Tests, die nur die fachlich relevanten Felder angeben, nicht jedes
    Mal das volle Schema ausschreiben muessen."""
    change = json.loads(json.dumps(EMPTY_CHANGE))
    change.update(overrides)
    change["knowledge_updates"] = _with_defaults(change["knowledge_updates"], KNOWLEDGE_UPDATE_DEFAULTS)
    change["decisions_add"] = _with_defaults(change["decisions_add"], DECISION_ADD_DEFAULTS)
    change["decisions_update"] = _with_defaults(change["decisions_update"], DECISION_UPDATE_DEFAULTS)
    change["open_loops_add"] = _with_defaults(change["open_loops_add"], OPEN_LOOP_ADD_DEFAULTS)
    change["open_loops_close"] = _with_defaults(change["open_loops_close"], OPEN_LOOP_CLOSE_DEFAULTS)
    return change


class FakeLlm:
    """Test-Double fuer llm.call_llm. Liefert der Reihe nach vordefinierte
    Antworten oder wirft konfigurierte Fehler."""

    def __init__(self, responses):
        from polaris_curator.llm import LlmResult

        self._LlmResult = LlmResult
        self.responses = list(responses)
        self.calls: list[str] = []
        self.images: list[list[tuple[str, bytes]]] = []

    def __call__(self, prompt: str, cfg, images=None, schema=None):
        self.calls.append(prompt)
        self.images.append(list(images or []))
        if not self.responses:
            raise AssertionError("FakeLlm: keine weitere Antwort konfiguriert")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return self._LlmResult(data=item, model=cfg.model, tokens_used=100)
