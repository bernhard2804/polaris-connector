"""Local OpenTelemetry JSONL export with prompt/tool content disabled."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult, SpanExporter
from pydantic_ai.capabilities.instrumentation import Instrumentation
from pydantic_ai.models.instrumented import InstrumentationSettings


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)


class JsonlSpanExporter(SpanExporter):
    def __init__(self, path: Path) -> None:
        self.path = path

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            for span in spans:
                context = span.context
                parent = span.parent
                payload = {
                    "schema": "polaris.otel-span.v1",
                    "name": span.name,
                    "trace_id": f"{context.trace_id:032x}" if context else None,
                    "span_id": f"{context.span_id:016x}" if context else None,
                    "parent_span_id": f"{parent.span_id:016x}" if parent else None,
                    "started_at": datetime.fromtimestamp(span.start_time / 1e9, timezone.utc).isoformat()
                    if span.start_time
                    else None,
                    "ended_at": datetime.fromtimestamp(span.end_time / 1e9, timezone.utc).isoformat()
                    if span.end_time
                    else None,
                    "status": span.status.status_code.name,
                    "attributes": _jsonable(dict(span.attributes or {})),
                }
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        return SpanExportResult.SUCCESS


@dataclass
class Telemetry:
    capability: Instrumentation
    provider: TracerProvider

    def close(self) -> None:
        self.provider.force_flush()
        self.provider.shutdown()


def configure_telemetry(path: Path) -> Telemetry:
    provider = TracerProvider(resource=Resource.create({"service.name": "polaris-agent-runtime-poc"}))
    provider.add_span_processor(SimpleSpanProcessor(JsonlSpanExporter(path)))
    settings = InstrumentationSettings(
        tracer_provider=provider,
        include_binary_content=False,
        include_content=False,
        include_model_request_parameters=False,
    )
    return Telemetry(capability=Instrumentation(settings=settings), provider=provider)
