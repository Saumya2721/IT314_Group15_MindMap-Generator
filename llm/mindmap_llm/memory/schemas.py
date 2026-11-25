"""
Schemas and data models for User Memory Extraction, Scoring, and Retrieval (S3-L3).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator


class UserMemory(BaseModel):
    """Represents a stored user memory record retrieved from the backend (S3-BE4)."""

    id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    map_id: UUID | None = None
    kind: str = Field(default="fact", description="preference | decision | fact")
    memory_type: str = Field(default="preference", description="e.g. preference, fact, decision, convention")
    content: str = Field(..., min_length=1, max_length=4000)
    importance_score: float = Field(default=0.8, ge=0.0, le=1.0)
    is_active: bool = True
    disabled_at: datetime | None = None
    last_accessed_at: datetime | None = None
    scope: str = Field(default="global", description="global | map")
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ProposedMemory(BaseModel):
    """Represents a newly extracted memory candidate before or during proposal."""

    content: str = Field(..., min_length=1, max_length=4000)
    kind: str = Field(default="fact", description="preference | decision | fact")
    memory_type: str = Field(default="preference", description="preference | fact | decision | convention")
    importance_score: float = Field(default=0.5, ge=0.0, le=1.0)
    is_durable: bool = Field(default=True, description="True if useful across multiple maps/sessions")

    @model_validator(mode="before")
    @classmethod
    def normalize_kind(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        raw_kind = data.get("kind") or data.get("memory_type") or "preference"
        kind = raw_kind if raw_kind in ("preference", "decision", "fact") else "fact"
        return {**data, "kind": kind, "memory_type": data.get("memory_type") or kind}


class MemoryExtractionPayload(BaseModel):
    """Structured output expected from LLM memory extraction prompt."""

    memories: list[ProposedMemory] = Field(default_factory=list)


class MemoryProposalRequest(BaseModel):
    """Payload sent to backend POST /internal/memory/propose (matching S3-BE4)."""

    user_id: UUID
    map_id: UUID | None = None
    kind: str = Field(default="fact", description="preference | decision | fact")
    memory_type: str = Field(default="preference", max_length=100)
    content: str = Field(..., min_length=1, max_length=4000)
    importance: float = Field(default=0.8, ge=0.0, le=1.0)


class MemoryProposalResponse(BaseModel):
    """Response returned from backend POST /internal/memory/propose."""

    stored: bool
    reason: str
    id: UUID | None = None
