"""Model adapter boundary; task logic never imports provider SDKs."""

from __future__ import annotations

import os
from typing import Any

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from .contracts import ModelRoute


def _latest_user_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        for part in reversed(message.parts):
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                return part.content
    return ""


def deterministic_model() -> FunctionModel:
    """Offline model double that still exercises the real agent/tool loop."""

    async def respond(messages: list[Any], _info: Any) -> ModelResponse:
        usage = RequestUsage(input_tokens=32, output_tokens=8)
        latest_parts = messages[-1].parts
        tool_return = next((part for part in latest_parts if isinstance(part, ToolReturnPart)), None)
        if tool_return is not None:
            return ModelResponse(
                parts=[TextPart(f"Tool-Ergebnis ausgewertet: {str(tool_return.content)[:500]}")],
                usage=usage,
                provider_name="offline",
            )

        user_text = _latest_user_text(messages).casefold()
        if "fort" in user_text or "detail" in user_text:
            call = ToolCallPart("read_project_file", {"path": "architecture.md"})
        else:
            call = ToolCallPart("search_project", {"pattern": "runtime"})
        return ModelResponse(parts=[call], usage=usage, provider_name="offline")

    return FunctionModel(respond, model_name="polaris-deterministic")


def resolve_model(route: ModelRoute) -> object:
    if route.provider == "offline":
        return deterministic_model()

    if route.base_url and route.provider == "ollama":
        from pydantic_ai.models.ollama import OllamaModel
        from pydantic_ai.providers.ollama import OllamaProvider

        model_name = route.model.split(":", 1)[-1]
        return OllamaModel(model_name, provider=OllamaProvider(base_url=route.base_url))

    if route.base_url and route.provider == "openai-compatible":
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        api_key = os.getenv(route.api_key_env) if route.api_key_env else None
        if not api_key:
            raise ValueError("the configured OpenAI-compatible endpoint requires its api_key_env variable")
        model_name = route.model.split(":", 1)[-1]
        return OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(base_url=route.base_url, api_key=api_key),
        )

    return route.model
