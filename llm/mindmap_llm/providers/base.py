"""
Abstract interface for the primary (text-generating) LLM.

Every concrete provider (Anthropic, OpenAI, a local vLLM server, whatever
Developer wants per FR-28) implements this one interface. Orchestration
code (orchestration/nodes.py) only ever talks to LLMProvider — it never
imports a vendor SDK directly. That's what "generic" buys us: swapping
models is a one-line env var change plus a new ~40-line adapter, not a
rewrite of the graph.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolSpec:
    """Describes a tool the LLM may call, in a vendor-neutral shape."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    call_id: str


@dataclass
class LLMResponse:
    """Vendor-neutral response shape. Exactly one of text / tool_calls is
    typically populated for a given turn, but both are allowed (some
    providers interleave text + tool use)."""

    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None
    usage_input_tokens: int | None = None
    usage_output_tokens: int | None = None
    raw: Any = None  # original provider payload, for debugging only


@dataclass
class Message:
    role: str  # "user" | "assistant" | "tool"
    content: str
    tool_call_id: str | None = None  # set when role == "tool"


class LLMProvider(ABC):
    """Implement this once per vendor. See providers/groq_provider.py
    for a reference implementation."""

    @abstractmethod
    async def generate(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        """Single generation turn. Callers handle any tool-call loop —
        this method does not loop internally, keeping it easy to mock
        deterministically in tests (US-13)."""
        raise NotImplementedError

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError


class SupportsHealthCheck(Protocol):
    async def health_check(self) -> bool: ...
