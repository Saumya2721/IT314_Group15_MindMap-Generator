"""Pydantic schemas for the S3-BE4 Memory API."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class MemoryItem(BaseModel):
    """Single agent memory row returned from the API."""

    id: UUID
    user_id: UUID
    map_id: UUID | None = None
    memory_type: str
    content: str
    is_active: bool
    created_at: datetime
    updated_at: datetime | None = None


class MemoryListResponse(BaseModel):
    items: list[MemoryItem]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class MemoryPatchRequest(BaseModel):
    """PATCH /memories/{id} — edit content and/or toggle active state."""

    content: str | None = Field(None, min_length=1, max_length=4000)
    is_active: bool | None = None
    memory_type: str | None = Field(None, max_length=100)


class MemoryProposeRequest(BaseModel):
    """POST /internal/memory/propose — worker write-path."""

    user_id: UUID
    map_id: UUID | None = None
    memory_type: str = Field(..., max_length=100)
    content: str = Field(..., min_length=1, max_length=4000)


class MemoryProposeResponse(BaseModel):
    stored: bool
    reason: str  # "created" | "duplicate" | "near_duplicate" | "limit_exceeded"
    id: UUID | None = None


# ---------------------------------------------------------------------------
# GDPR
# ---------------------------------------------------------------------------


class GDPRExportResponse(BaseModel):
    user_id: str
    exported_at: str
    total_memories: int
    memories: list[dict[str, Any]]
    notice: str
