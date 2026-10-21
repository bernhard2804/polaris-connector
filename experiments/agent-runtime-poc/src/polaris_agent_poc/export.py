"""Versioned, provider-neutral append-only audit/exit export."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai_harness.step_persistence import RunRecord, StepEvent

from .contracts import ModelRoute, SessionSummary


class SessionExporter:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _known_ids(self) -> set[str]:
        if not self.path.exists():
            return set()
        ids: set[str] = set()
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                value = json.loads(line)
                if isinstance(value.get("event_id"), str):
                    ids.add(value["event_id"])
        return ids

    def append(
        self,
        *,
        session_id: str,
        route: ModelRoute,
        records: Iterable[RunRecord],
        events: Iterable[StepEvent],
        messages: Iterable[ModelMessage],
        summary: SessionSummary,
    ) -> None:
        known = self._known_ids()
        payloads: list[dict[str, object]] = [
            {
                "schema": "polaris.session.v1",
                "event_id": f"session:{session_id}",
                "event_type": "session.opened",
                "session_id": session_id,
                "profile": route.profile,
                "model_class": route.model_class,
            }
        ]
        for record in records:
            payloads.append(
                {
                    "schema": "polaris.session.v1",
                    "event_id": f"run:{record.run_id}",
                    "event_type": "run.registered",
                    "session_id": session_id,
                    "run_id": record.run_id,
                    "parent_run_id": record.parent_run_id,
                    "agent_role": record.agent_name,
                    "timestamp": record.started_at.isoformat(),
                    "provider": route.provider,
                    "model": route.model,
                }
            )
        for event in events:
            key = event.idempotency_key or f"{event.timestamp.isoformat()}:{event.kind}"
            payloads.append(
                {
                    "schema": "polaris.session.v1",
                    "event_id": f"step:{event.run_id}:{key}",
                    "event_type": event.kind.replace("_", "."),
                    "session_id": session_id,
                    "run_id": event.run_id,
                    "parent_run_id": event.parent_run_id,
                    "agent_role": event.agent_name,
                    "step": event.step_index,
                    "timestamp": event.timestamp.isoformat(),
                    "tool_call_id": event.tool_call_id,
                    "tool_name": event.tool_name,
                    "error": event.error,
                }
            )
        for index, message in enumerate(messages):
            message_run_id = getattr(message, "run_id", None)
            timestamp = getattr(message, "timestamp", None)
            event_id = f"message:{message_run_id or session_id}:{timestamp.isoformat() if timestamp else index}"
            payloads.append(
                {
                    "schema": "polaris.session.v1",
                    "event_id": event_id,
                    "event_type": "transcript.message",
                    "session_id": session_id,
                    "run_id": message_run_id,
                    "timestamp": timestamp.isoformat() if timestamp else None,
                    "role": "request" if isinstance(message, ModelRequest) else "assistant",
                    "instructions": getattr(message, "instructions", None),
                    "parts": [_normalise_part(part) for part in message.parts],
                }
            )
        payloads.append(
            {
                **summary.to_dict(),
                "schema": "polaris.session.v1",
                "summary_schema": summary.schema,
                "event_id": f"summary:{summary.run_id}",
                "event_type": "run.summary",
            }
        )

        new_payloads = [payload for payload in payloads if payload["event_id"] not in known]
        if not new_payloads:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            for payload in new_payloads:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)


def _normalise_part(part: object) -> dict[str, object]:
    if isinstance(part, UserPromptPart):
        return {"type": "user_text", "content": _json_value(part.content)}
    if isinstance(part, TextPart):
        return {"type": "assistant_text", "content": part.content}
    if isinstance(part, ThinkingPart):
        return {"type": "thinking", "content": part.content}
    if isinstance(part, ToolCallPart):
        return {
            "type": "tool_call",
            "tool_name": part.tool_name,
            "tool_call_id": part.tool_call_id,
            "arguments": _json_value(part.args),
        }
    if isinstance(part, ToolReturnPart):
        return {
            "type": "tool_result",
            "tool_name": part.tool_name,
            "tool_call_id": part.tool_call_id,
            "content": _json_value(part.content),
        }
    return {
        "type": "other",
        "part_kind": str(getattr(part, "part_kind", "unknown")),
        "content": _json_value(getattr(part, "content", None)),
    }
