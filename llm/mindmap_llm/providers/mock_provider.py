"""Mock LLMProvider for testing and offline fallback scenarios (S3-L5)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from providers.base import LLMProvider, LLMResponse, Message, ToolCall, ToolSpec

DEFAULT_MOCK_MINDMAP = json.dumps(
    {
        "nodes": [
            {
                "id": "n1",
                "label": "Mock Root",
                "description": "Deterministic root concept for testing",
                "node_type": "root",
                "sources": ["seed"],
            },
            {
                "id": "n2",
                "label": "Child Concept 1",
                "description": "Deterministic child branch 1",
                "node_type": "default",
                "sources": ["seed"],
            },
            {
                "id": "n3",
                "label": "Child Concept 2",
                "description": "Deterministic child branch 2",
                "node_type": "default",
                "sources": ["seed"],
            },
        ],
        "edges": [
            {"source": "n1", "target": "n2", "label": "relates to"},
            {"source": "n1", "target": "n3", "label": "relates to"},
        ],
    }
)


class MockProvider(LLMProvider):
    """Deterministic mock provider returning scripted or default JSON responses."""

    def __init__(
        self,
        model: str = "mock-model",
        responses: Sequence[str | LLMResponse] | None = None,
        tool_calls: Sequence[ToolCall] | None = None,
        scripted_tool_calls: Sequence[Sequence[ToolCall]] | None = None,
        **kwargs: Any,
    ):
        self._model = model
        self._responses: list[str | LLMResponse] = list(responses) if responses else [DEFAULT_MOCK_MINDMAP]
        self._tool_calls: list[ToolCall] = list(tool_calls) if tool_calls is not None else []
        self._initial_scripted_tool_calls: list[list[ToolCall]] = (
            [list(tcs) for tcs in scripted_tool_calls] if scripted_tool_calls is not None else []
        )
        self._scripted_tool_calls: list[list[ToolCall]] = [list(tcs) for tcs in self._initial_scripted_tool_calls]
        self._index = 0
        self.calls: list[list[Message]] = []
        self.last_response_format: dict[str, Any] | None = None
        self._supports_structured_output: bool = True

    @property
    def name(self) -> str:
        return f"mock:{self._model}"

    @property
    def supports_structured_output(self) -> bool:
        return self._supports_structured_output

    @supports_structured_output.setter
    def supports_structured_output(self, value: bool) -> None:
        self._supports_structured_output = value

    def reset(self) -> None:
        """Reset call history, response counter, and restore scripted tool calls."""
        self._index = 0
        self.calls.clear()
        self._scripted_tool_calls = [list(tcs) for tcs in self._initial_scripted_tool_calls]

    def set_responses(self, responses: Sequence[str | LLMResponse]) -> None:
        """Update scripted responses."""
        self._responses = list(responses) if responses else [DEFAULT_MOCK_MINDMAP]
        self._index = 0

    def add_response(self, response: str | LLMResponse) -> None:
        """Append a scripted response."""
        self._responses.append(response)

    async def generate(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        self.calls.append(messages)
        self.last_response_format = response_format

        # Check for scripted sequence of tool calls first
        if self._scripted_tool_calls:
            tcs = self._scripted_tool_calls.pop(0)
            return LLMResponse(
                text=None,
                tool_calls=tcs,
                stop_reason="tool_use",
                usage_input_tokens=15,
                usage_output_tokens=30,
            )

        # If static tool_calls configured on provider, return them
        if self._tool_calls:
            return LLMResponse(
                text=None,
                tool_calls=list(self._tool_calls),
                stop_reason="tool_use",
                usage_input_tokens=15,
                usage_output_tokens=30,
            )

        resp_item = self._responses[self._index % len(self._responses)]
        self._index += 1

        if isinstance(resp_item, LLMResponse):
            return resp_item

        return LLMResponse(
            text=resp_item,
            tool_calls=[],
            stop_reason="stop",
            usage_input_tokens=10,
            usage_output_tokens=50,
        )
