"""
Tool interface (FR-08, FR-29, FR-40). New tools are added by implementing
Tool and registering an instance in tools/registry.py — orchestration/
never imports a specific tool module, only this interface plus the
registry, satisfying US-12's "add a tool without touching core
orchestration" requirement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from providers.base import ToolSpec


@dataclass
class ToolResult:
    content: str
    source_title: str | None
    source_url: str  # required: embeddings.chk_embeddings_source_url needs this for non-seed sources


class ToolError(Exception):
    """Raised on timeout/failure. Callers apply the tool's own fallback
    behaviour (US-12 Scenario 'Tool Failure') — generation continues
    without that tool's results rather than failing the whole job."""


class Tool(ABC):
    @property
    @abstractmethod
    def spec(self) -> ToolSpec:
        """Vendor-neutral description the LLMProvider uses to offer this
        tool to the model."""
        raise NotImplementedError

    @abstractmethod
    async def invoke(self, **kwargs) -> list[ToolResult]:
        raise NotImplementedError
