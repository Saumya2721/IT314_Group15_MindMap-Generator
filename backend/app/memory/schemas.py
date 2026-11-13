from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class MemoryMessage(BaseModel):
    """Single message in memory context."""

    role: str
    content: str
    tokens: int = 0


class MemoryContext(BaseModel):
    """Assembled memory context ready to pass to LLM."""

    messages: list[MemoryMessage]
    summary: str | None = None
    total_tokens: int
    budget_remaining: int
    is_truncated: bool


class RollingSummary(BaseModel):
    """Stored rolling summary for a mind map chat thread."""

    map_id: str
    summary_text: str
    message_count: int
    last_updated: datetime


class TokenBudget(BaseModel):
    """Token budget configuration for context assembly."""

    max_total_tokens: int = 4096
    max_history_tokens: int = 2048
    max_summary_tokens: int = 512
    reserved_for_response: int = 1024


class CallbackPayload(BaseModel):
    """Payload for internal job-completion callback."""

    map_id: str
    job_id: str
    status: str
    summary: str | None = None
    error: str | None = None
