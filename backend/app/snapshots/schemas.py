from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class SnapshotResponse(BaseModel):
    id: UUID
    map_id: UUID
    version_number: int | None = None
    trigger_source: str | None = None
    trigger_job_id: UUID | None = None
    target_node_id: UUID | None = None
    reason: str = "Manual snapshot"
    label: str | None = None
    snapshot_data: dict[str, Any] = Field(default_factory=dict)
    nodes_count: int = 0
    edges_count: int = 0
    created_by: UUID | None = None
    created_at: datetime


class SnapshotRestoreResponse(BaseModel):
    status: str = "restored"
    map_id: str
    restored_version: int
    new_version: int
    version: int | None = None
    nodes_restored: int | None = None
    edges_restored: int | None = None
    snapshot_id: str
    snapshot_data: dict[str, Any] = Field(default_factory=dict)
