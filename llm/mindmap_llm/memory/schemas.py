"""
Schemas and data models for User Memory Extraction, Scoring, and Retrieval (S3-L3).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class UserMemory(BaseModel):
    """Represents a stored user memory record retrieved from the backend (S3-BE4)."""

    id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    map_id: UUID | None = None
    memory_type: str = Field(default="preference", description="e.g. preference, fact, decision, convention")
    content: str = Field(..., min_length=1, max_length=4000)
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None
    importance_score: float = Field(default=0.8, ge=0.0, le=1.0)


class ProposedMemory(BaseModel):
    """Represents a newly extracted memory candidate before or during proposal."""

    content: str = Field(..., min_length=1, max_length=4000)
    memory_type: str = Field(default="preference", description="preference | fact | decision | convention")
    importance_score: float = Field(default=0.5, ge=0.0, le=1.0)
    is_durable: bool = Field(default=True, description="True if useful across multiple maps/sessions")


class MemoryExtractionPayload(BaseModel):
    """Structured output expected from LLM memory extraction prompt."""

    memories: list[ProposedMemory] = Field(default_factory=list)


class MemoryProposalRequest(BaseModel):
    """Payload sent to backend POST /internal/memory/propose (matching S3-BE4)."""

    user_id: UUID
    map_id: UUID | None = None
    memory_type: str = Field(..., max_length=100)
    content: str = Field(..., min_length=1, max_length=4000)


class MemoryProposalResponse(BaseModel):
    """Response returned from backend POST /internal/memory/propose."""

    stored: bool
    reason: str
    id: UUID | None = None
