"""Thin adapter around Pydantic AI and Harness-owned runtime behavior."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from uuid import uuid4

from pydantic_ai import Agent
from pydantic_ai_harness.step_persistence import FileStepStore, StepPersistence, continue_run

from .config import RuntimeConfig
from .contracts import PermissionClass, SessionSummary
from .export import SessionExporter
from .models import resolve_model
from .telemetry import configure_telemetry
from .tools import make_read_only_tools

_VALID_SESSION_ID = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")

STABLE_INSTRUCTIONS = """You are the Polaris read-only project reader.
Use only the supplied read-only tools. Inspect evidence before answering.
Never claim to have changed files, used the network, or written long-term memory.
Keep the answer concise and cite project-relative file names.
"""


@dataclass(frozen=True)
class RunOutcome:
    output: str
    summary: SessionSummary
    export_path: Path
    telemetry_path: Path


class AgentRuntime:
    def __init__(self, config: RuntimeConfig) -> None:
        if config.policy.permissions != {PermissionClass.READ_ONLY}:
            raise ValueError("only read-only policy is implemented by this PoC")
        self.config = config
        self.store = FileStepStore(config.state_dir / "step-store", max_snapshots_per_run=3)

    async def run(
        self,
        *,
        session_id: str,
        prompt: str,
        resume: bool = False,
        parent_session_id: str | None = None,
    ) -> RunOutcome:
        if not _VALID_SESSION_ID.fullmatch(session_id) or ".." in session_id:
            raise ValueError("session_id must be path-safe")
        if parent_session_id and (not _VALID_SESSION_ID.fullmatch(parent_session_id) or ".." in parent_session_id):
            raise ValueError("parent_session_id must be path-safe")

        history = None
        if resume:
            prior_runs = await self.store.list_runs(conversation_id=session_id)
            if not prior_runs:
                raise LookupError(f"no persisted run for session {session_id!r}")
            prior = prior_runs[-1]
            unresolved = await self.store.list_unresolved_tool_effects(run_id=prior.run_id)
            if unresolved:
                names = ", ".join(effect.tool_name for effect in unresolved)
                raise RuntimeError(f"resume refused: unresolved tool effects: {names}")
            history = await continue_run(self.store, run_id=prior.run_id)

        self.config.state_dir.mkdir(parents=True, exist_ok=True)
        telemetry_path = self.config.state_dir / "telemetry" / f"{session_id}.jsonl"
        telemetry = configure_telemetry(telemetry_path)
        run_id = str(uuid4())
        persistence = StepPersistence(
            store=self.store,
            agent_name=self.config.agent_role,
            run_id=run_id,
            metadata={
                "provider": self.config.route.provider,
                "model": self.config.route.model,
                "profile": self.config.route.profile,
            },
        )
        agent = Agent(
            resolve_model(self.config.route),
            name=self.config.agent_role,
            instructions=STABLE_INSTRUCTIONS,
            tools=make_read_only_tools(self.config.workspace),
            capabilities=[telemetry.capability, persistence],
        )

        started = datetime.now(timezone.utc)
        timer = monotonic()
        result = None
        failure: Exception | None = None
        try:
            result = await agent.run(
                prompt,
                message_history=history,
                conversation_id=session_id,
                run_id=run_id,
            )
        except Exception as exc:
            failure = exc
        finally:
            telemetry.close()
        ended = datetime.now(timezone.utc)

        events = await self.store.list_events(run_id=run_id)
        usage = result.usage if result is not None else None
        usage_available = usage is not None and self.config.route.reports_usage
        cache_usage_available = usage_available and self.config.route.reports_cache_usage
        cost = getattr(usage, "cost", None) if usage is not None else None
        summary = SessionSummary(
            schema="polaris.session-summary.v1",
            session_id=session_id,
            parent_session_id=parent_session_id,
            run_id=run_id,
            agent_role=self.config.agent_role,
            provider=self.config.route.provider,
            model=self.config.route.model,
            started_at=started.isoformat(),
            ended_at=ended.isoformat(),
            steps=max((event.step_index for event in events), default=0),
            tool_calls=usage.tool_calls
            if usage is not None
            else sum(event.kind == "tool_call_started" for event in events),
            input_tokens=usage.input_tokens if usage_available and usage is not None else None,
            cached_input_tokens=usage.cache_read_tokens if cache_usage_available and usage is not None else None,
            cache_write_tokens=usage.cache_write_tokens if cache_usage_available and usage is not None else None,
            output_tokens=usage.output_tokens if usage_available and usage is not None else None,
            cache_hit_rate=usage.cache_hit_ratio
            if cache_usage_available and usage is not None and usage.input_tokens
            else None,
            cost_usd=str(cost) if cost is not None else None,
            duration_ms=round((monotonic() - timer) * 1000),
            compactions=0,
            memory_reads=0,
            memory_writes=0,
            errors=[f"{type(failure).__name__}: {failure}"] if failure is not None else [],
            exit_status="failed" if failure is not None else "completed",
        )

        metrics_path = self.config.state_dir / "metrics" / f"{session_id}.jsonl"
        metrics_path.parent.mkdir(parents=True, exist_ok=True)
        with metrics_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(summary.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")

        records = await self.store.list_runs(conversation_id=session_id)
        all_events = []
        for record in records:
            all_events.extend(await self.store.list_events(run_id=record.run_id))
        export_path = self.config.state_dir / "exports" / f"{session_id}.jsonl"
        SessionExporter(export_path).append(
            session_id=session_id,
            route=self.config.route,
            records=records,
            events=all_events,
            messages=result.all_messages() if result is not None else [],
            summary=summary,
        )
        if failure is not None:
            raise failure
        assert result is not None
        return RunOutcome(
            output=str(result.output),
            summary=summary,
            export_path=export_path,
            telemetry_path=telemetry_path,
        )
