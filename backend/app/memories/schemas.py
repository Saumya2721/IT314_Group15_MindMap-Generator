from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class MemoryCreateRequest(BaseModel):
    """Payload to create a new user or map memory."""

    content: str = Field(..., min_length=3, max_length=2000, description="Memory statement or fact.")
    category: str = Field(
        default="general",
        description="Category: preference, fact, instruction, constraint, general.",
    )
    scope: Literal["global", "map"] = Field(
        default="global",
        description="Scope: 'global' applies across all maps, 'map' applies to target map.",
    )
    map_id: UUID | None = Field(
        default=None,
        description="Target map UUID when scope is 'map'. Must be null for 'global'.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary metadata dictionary.")


class MemoryUpdateRequest(BaseModel):
    """Payload to update an existing memory."""

    content: str | None = Field(
        default=None,
        min_length=3,
        max_length=2000,
        description="Updated memory content.",
    )
    category: str | None = Field(default=None, description="Updated category.")
    metadata: dict[str, Any] | None = Field(default=None, description="Updated metadata dictionary.")


class MemoryResponse(BaseModel):
    """Public representation of a memory record."""

    id: UUID
    user_id: UUID
    map_id: UUID | None = None
    content: str
    category: str
    scope: str
    is_active: bool
    content_hash: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class MemoryListResponse(BaseModel):
    """Paginated list response of memories."""

    items: list[MemoryResponse]
    total: int
    limit: int
    offset: int


class GDPRExportResponse(BaseModel):
    """Machine-readable GDPR memory export bundle."""

    user_id: str
    export_timestamp: datetime
    compliance_statement: str
    total_memories: int
    memories: list[MemoryResponse]
    associated_map_summaries: list[dict[str, Any]] = Field(default_factory=list)


class GDPRDeleteResponse(BaseModel):
    """Confirmation receipt of GDPR memory purge."""

    user_id: str
    deleted_count: int
    status: str = "purged"
    confirmation_timestamp: datetime
