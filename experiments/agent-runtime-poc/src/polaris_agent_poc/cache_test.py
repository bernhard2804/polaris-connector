"""Opt-in A/B test for provider-reported prompt-cache usage."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from time import monotonic
from uuid import uuid4

from pydantic_ai import Agent

from .config import RuntimeConfig
from .models import resolve_model

_FIXED_PREFIX = "\n".join(
    f"Stable protocol rule {index:03d}: preserve evidence, tool schemas, ordering, and prior messages."
    for index in range(180)
)


async def run_cache_test(config: RuntimeConfig, *, steps: int, output: Path) -> dict[str, object]:
    if not config.route.reports_cache_usage:
        raise ValueError("cache tests require a profile with provider-reported cache usage")
    if not 20 <= steps <= 50:
        raise ValueError("steps must be between 20 and 50")

    output.parent.mkdir(parents=True, exist_ok=True)
    summaries: dict[str, object] = {}
    with output.open("a", encoding="utf-8") as handle:
        for variant in ("stable-prefix", "mutated-prefix"):
            history = None
            rows: list[dict[str, object]] = []
            started = monotonic()
            conversation_id = f"cache-{variant}-{uuid4()}"
            for step in range(steps):
                mutation = "" if variant == "stable-prefix" else f"MUTATION-{step:03d}\n"
                instructions = mutation + _FIXED_PREFIX
                agent = Agent(resolve_model(config.route), instructions=instructions, name="cache-probe")
                result = await agent.run(
                    f"Step {step + 1}/{steps}: answer only ACK-{step + 1}.",
                    message_history=history,
                    conversation_id=conversation_id,
                )
                history = result.all_messages()
                usage = result.usage
                row = {
                    "schema": "polaris.cache-sample.v1",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "variant": variant,
                    "step": step + 1,
                    "provider": config.route.provider,
                    "model": config.route.model,
                    "input_tokens": usage.input_tokens,
                    "cached_input_tokens": usage.cache_read_tokens,
                    "cache_write_tokens": usage.cache_write_tokens,
                    "output_tokens": usage.output_tokens,
                    "cost_usd": str(usage.cost) if usage.cost is not None else None,
                }
                rows.append(row)
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

            input_tokens = sum(int(row["input_tokens"]) for row in rows)
            cached_tokens = sum(int(row["cached_input_tokens"]) for row in rows)
            costs = [row["cost_usd"] for row in rows]
            summary = {
                "schema": "polaris.cache-summary.v1",
                "variant": variant,
                "steps": steps,
                "input_tokens": input_tokens,
                "cached_input_tokens": cached_tokens,
                "cache_hit_rate": cached_tokens / input_tokens if input_tokens else None,
                "output_tokens": sum(int(row["output_tokens"]) for row in rows),
                "cost_usd": str(sum(Decimal(str(value)) for value in costs))
                if all(value is not None for value in costs)
                else None,
                "duration_ms": round((monotonic() - started) * 1000),
            }
            summaries[variant] = summary
            handle.write(json.dumps(summary, ensure_ascii=False, sort_keys=True) + "\n")
    return summaries
