from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path

import pytest

from polaris_agent_poc.cache_test import run_cache_test
from polaris_agent_poc.config import load_config
from polaris_agent_poc.contracts import PermissionClass
from polaris_agent_poc.runtime import AgentRuntime
from polaris_agent_poc.tools import make_read_only_tools

ROOT = Path(__file__).parents[1]


async def _run_with_test_heartbeat(awaitable):
    # Some command sandboxes do not promptly wake their asyncio selector for
    # AnyIO worker-thread callbacks. A timer keeps this test portable without
    # changing the production runtime or persistence adapter.
    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(0.01)

    task = asyncio.create_task(heartbeat())
    try:
        return await awaitable
    finally:
        task.cancel()


def _config(tmp_path: Path):
    base = load_config(ROOT / "config.example.toml", "offline")
    return replace(base, state_dir=tmp_path / "state")


def test_config_is_read_only_and_profiles_are_provider_neutral() -> None:
    offline = load_config(ROOT / "config.example.toml", "offline")
    ollama = load_config(ROOT / "config.example.toml", "ollama")
    cloud = load_config(ROOT / "config.example.toml", "anthropic")

    assert offline.policy.permissions == {PermissionClass.READ_ONLY}
    assert ollama.route.provider == "ollama"
    assert cloud.route.provider == "anthropic"
    assert ollama.workspace == cloud.workspace == ROOT / "fixture-workspace"


def test_read_tool_rejects_workspace_escape() -> None:
    read_file = make_read_only_tools(ROOT / "fixture-workspace")[2]
    with pytest.raises(ValueError, match="escapes"):
        asyncio.run(read_file("../config.example.toml"))


def test_start_and_resume_persist_export_and_trace(tmp_path: Path) -> None:
    config = _config(tmp_path)
    first = asyncio.run(
        _run_with_test_heartbeat(
            AgentRuntime(config).run(
                session_id="test-session",
                prompt="Durchsuche das Projekt nach Runtime-Hinweisen.",
            )
        )
    )
    second = asyncio.run(
        _run_with_test_heartbeat(
            AgentRuntime(config).run(
                session_id="test-session",
                prompt="Setze die Arbeit fort und lies die Architekturdatei im Detail.",
                resume=True,
            )
        )
    )

    assert first.summary.run_id != second.summary.run_id
    assert first.summary.tool_calls == second.summary.tool_calls == 1
    assert first.summary.input_tokens is None
    assert first.summary.cached_input_tokens is None
    assert "architecture.md" in first.output
    assert "Fixture Architecture" in second.output

    runs = list((config.state_dir / "step-store").glob("*/run.json"))
    assert len(runs) == 2
    assert all((path.parent / "events.jsonl").exists() for path in runs)
    assert all((path.parent / "tool_effects.jsonl").exists() for path in runs)

    exported = [json.loads(line) for line in second.export_path.read_text(encoding="utf-8").splitlines()]
    event_types = [event["event_type"] for event in exported]
    assert event_types.count("run.registered") == 2
    assert event_types.count("run.summary") == 2
    assert "tool.call.started" in event_types
    assert "tool.call.completed" in event_types
    assert event_types.count("transcript.message") == 8
    assert all(event["schema"] == "polaris.session.v1" for event in exported)
    transcript = [event for event in exported if event["event_type"] == "transcript.message"]
    assert any("Durchsuche das Projekt" in json.dumps(event) for event in transcript)
    assert any("Fixture Architecture" in json.dumps(event) for event in transcript)

    spans = [json.loads(line) for line in second.telemetry_path.read_text(encoding="utf-8").splitlines()]
    span_names = {span["name"] for span in spans}
    assert "invoke_agent project-reader" in span_names
    assert "execute_tool search_project" in span_names
    assert "execute_tool read_project_file" in span_names
    serialized = json.dumps(spans)
    assert "Durchsuche das Projekt" not in serialized
    assert "Fixture Architecture" not in serialized


def test_cache_test_refuses_synthetic_offline_measurement(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with pytest.raises(ValueError, match="provider-reported"):
        asyncio.run(run_cache_test(config, steps=20, output=tmp_path / "cache.jsonl"))
