from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class SnapshotResponse(BaseModel):
    id: UUID
    map_id: UUID
    version_number: int
    trigger_source: str
    trigger_job_id: UUID | None = None
    label: str | None = None
    snapshot_data: dict[str, Any]
    created_by: UUID | None = None
    created_at: datetime


class SnapshotRestoreResponse(BaseModel):
    status: str = "restored"
    map_id: str
    restored_version: int
    new_version: int
    snapshot_id: str
    snapshot_data: dict[str, Any] = Field(default_factory=dict)
