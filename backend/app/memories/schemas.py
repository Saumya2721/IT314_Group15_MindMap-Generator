"""Pydantic schemas for the S3-BE4 Memory API."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class MemoryItem(BaseModel):
    """Single agent memory row returned from the API (S3-DB2 / S3-BE4)."""

    id: UUID
    user_id: UUID
    map_id: UUID | None = None
    kind: str = Field("fact", description="preference | decision | fact")
    memory_type: str = Field("preference", description="Legacy alias for kind")
    content: str
    importance: float = Field(1.0, ge=0.0, le=1.0)
    is_active: bool = True
    disabled_at: datetime | None = None
    last_accessed_at: datetime | None = None
    scope: str = Field("global", description="global | map")
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
    """PATCH /memories/{id} — edit content, toggle active state, update kind or importance."""

    content: str | None = Field(None, min_length=1, max_length=4000)
    is_active: bool | None = None
    kind: str | None = Field(None, pattern=r"^(preference|decision|fact)$")
    memory_type: str | None = Field(None, max_length=100)
    importance: float | None = Field(None, ge=0.0, le=1.0)

    @field_validator("content", mode="before")
    @classmethod
    def trim_content(cls, content: Any) -> Any:
        return content.strip() if isinstance(content, str) else content


class MemoryProposeRequest(BaseModel):
    """POST /internal/memory/propose — worker write-path."""

    user_id: UUID
    map_id: UUID | None = None
    kind: str | None = Field(None, pattern=r"^(preference|decision|fact)$")
    memory_type: str | None = Field(None, max_length=100)
    content: str = Field(..., min_length=1, max_length=4000)
    importance: float = Field(1.0, ge=0.0, le=1.0)


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
