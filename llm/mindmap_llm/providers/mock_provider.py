"""Mock LLMProvider for testing and offline fallback scenarios."""

from __future__ import annotations

from typing import Any

from providers.base import LLMProvider, LLMResponse, Message, ToolSpec


class MockProvider(LLMProvider):
    """Deterministic mock provider returning scripted or default JSON responses."""

    def __init__(
        self,
        model: str = "mock-model",
        responses: list[str] | None = None,
        **kwargs: Any,
    ):
        self._model = model
        default_resp = (
            '{"nodes": [{"id": "n1", "label": "Mock Root", "description": "Mock description", '
            '"node_type": "root", "sources": ["seed"]}], "edges": []}'
        )
        self._responses = list(responses) if responses else [default_resp]
        self._index = 0

    @property
    def name(self) -> str:
        return f"mock:{self._model}"

    async def generate(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        resp_text = self._responses[self._index % len(self._responses)]
        self._index += 1
        return LLMResponse(
            text=resp_text,
            tool_calls=[],
            stop_reason="stop",
            usage_input_tokens=10,
            usage_output_tokens=50,
        )
