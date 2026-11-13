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
    "/maps/{map_id}/snapshots/{version_no}/restore",
    response_model=SnapshotRestoreResponse,
    status_code=200,
)
@router.post(
    "/api/maps/{map_id}/snapshots/{version_no}/restore",
    response_model=SnapshotRestoreResponse,
    status_code=200,
)
async def restore_snapshot_by_version(
    map_id: UUID,
    version_no: str,
    request: Request,
    uid: UUID = Depends(current_user_id),
) -> dict[str, Any]:
    """Restore nodes and edges from a snapshot version into the active map, incrementing version."""
    pool = _get_pool(request)
    result = await restore_snapshot_by_version_or_id(
        pool=pool,
        map_id=map_id,
        identifier=version_no,
        user_id=uid,
    )
    if not result:
        raise HTTPException(
            status_code=404,
            detail=f"Snapshot '{version_no}' not found for map '{map_id}' or unauthorized.",
        )
    return result
