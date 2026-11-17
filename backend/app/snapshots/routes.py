from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from core.supabase import current_user_id
from fastapi import APIRouter, Depends, HTTPException, Request
from snapshots.schemas import SnapshotRestoreResponse
from snapshots.service import restore_snapshot_by_version_or_id

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Snapshots & Version History"])


def _get_pool(request: Request):
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(503, "Database pool not initialized")
    return pool


@router.post(
    "/maps/{map_id}/snapshots/{identifier}/restore",
    response_model=SnapshotRestoreResponse,
    status_code=200,
    summary="Restore snapshot by version number or snapshot UUID",
)
@router.post(
    "/api/maps/{map_id}/snapshots/{identifier}/restore",
    response_model=SnapshotRestoreResponse,
    status_code=200,
    include_in_schema=False,
)
async def restore_snapshot(
    map_id: UUID,
    identifier: str,
    request: Request,
    uid: UUID = Depends(current_user_id),
) -> dict[str, Any]:
    """Restore nodes and edges from a snapshot (by either version_number or snapshot UUID) into the active map, incrementing version."""
    pool = _get_pool(request)
    result = await restore_snapshot_by_version_or_id(
        pool=pool,
        map_id=map_id,
        identifier=identifier,
        user_id=uid,
    )
    if not result:
        raise HTTPException(
            status_code=404,
            detail=f"Snapshot '{identifier}' not found for map '{map_id}' or unauthorized.",
        )
    return result
