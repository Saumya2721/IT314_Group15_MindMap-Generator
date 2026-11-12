from __future__ import annotations

import logging
from typing import Any, Literal
from uuid import UUID

from core.supabase import current_user_id
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from memories.schemas import (
    GDPRDeleteResponse,
    GDPRExportResponse,
    MemoryCreateRequest,
    MemoryListResponse,
    MemoryResponse,
    MemoryUpdateRequest,
)
from memories.service import (
    create_memory,
    delete_memory,
    gdpr_delete_user_memories,
    gdpr_export_user_memories,
    get_memory_by_id,
    list_memories,
    set_memory_active_status,
    update_memory,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memories", tags=["Memory Management & Preferences"])


def _get_pool(request: Request):
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database pool not initialized",
        )
    return pool


# ---------------------------------------------------------------------
# GDPR Endpoints (Must be declared before /{memory_id} to avoid UUID parse clash)
# ---------------------------------------------------------------------


@router.get("/gdpr/export", response_model=GDPRExportResponse, status_code=status.HTTP_200_OK)
@router.post("/gdpr/export", response_model=GDPRExportResponse, status_code=status.HTTP_200_OK)
async def export_user_memories_gdpr(
    request: Request,
    uid: UUID = Depends(current_user_id),
) -> dict[str, Any]:
    """Export all memory records and history for authenticated user under GDPR Article 15/20."""
    pool = _get_pool(request)
    return await gdpr_export_user_memories(pool, uid)


@router.delete("/gdpr/delete", response_model=GDPRDeleteResponse, status_code=status.HTTP_200_OK)
@router.post("/gdpr/delete", response_model=GDPRDeleteResponse, status_code=status.HTTP_200_OK)
async def delete_user_memories_gdpr(
    request: Request,
    uid: UUID = Depends(current_user_id),
) -> dict[str, Any]:
    """Execute GDPR Article 17 erasure hook, purging all memory records for authenticated user."""
    pool = _get_pool(request)
    return await gdpr_delete_user_memories(pool, uid)


# ---------------------------------------------------------------------
# Memory Collection CRUD
# ---------------------------------------------------------------------


@router.get("", response_model=MemoryListResponse, status_code=status.HTTP_200_OK)
async def get_memories(
    request: Request,
    scope: Literal["all", "global", "map"] = Query("all", description="Filter by scope"),
    map_id: UUID | None = Query(None, description="Optional map UUID"),
    is_active: bool | None = Query(None, description="Filter active/disabled memories"),
    category: str | None = Query(None, description="Filter by category"),
    search: str | None = Query(None, description="Text search in memory content"),
    limit: int = Query(50, ge=1, le=100, description="Page limit"),
    offset: int = Query(0, ge=0, description="Page offset"),
    uid: UUID = Depends(current_user_id),
) -> dict[str, Any]:
    """List memories for user with scope, category, and text filtering."""
    pool = _get_pool(request)
    items, total = await list_memories(
        pool=pool,
        user_id=uid,
        scope=scope,
        map_id=map_id,
        is_active=is_active,
        category=category,
        search=search,
        limit=limit,
        offset=offset,
    )
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("", response_model=MemoryResponse, status_code=status.HTTP_201_CREATED)
async def create_new_memory(
    payload: MemoryCreateRequest,
    request: Request,
    uid: UUID = Depends(current_user_id),
) -> dict[str, Any]:
    """Create a new user memory with content validation and deduplication."""
    pool = _get_pool(request)
    try:
        return await create_memory(pool, uid, payload)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        msg = str(exc)
        if "Duplicate" in msg or "Near-duplicate" in msg:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=msg) from exc
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg) from exc


# ---------------------------------------------------------------------
# Single Memory Operations
# ---------------------------------------------------------------------


@router.get("/{memory_id}", response_model=MemoryResponse, status_code=status.HTTP_200_OK)
async def get_single_memory(
    memory_id: UUID,
    request: Request,
    uid: UUID = Depends(current_user_id),
) -> dict[str, Any]:
    """Retrieve a single memory record by ID."""
    pool = _get_pool(request)
    mem = await get_memory_by_id(pool, memory_id, uid)
    if not mem:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Memory '{memory_id}' not found.",
        )
    return mem


@router.patch("/{memory_id}", response_model=MemoryResponse, status_code=status.HTTP_200_OK)
async def update_single_memory(
    memory_id: UUID,
    payload: MemoryUpdateRequest,
    request: Request,
    uid: UUID = Depends(current_user_id),
) -> dict[str, Any]:
    """Update memory content, category, or metadata."""
    pool = _get_pool(request)
    try:
        updated = await update_memory(pool, memory_id, uid, payload)
    except ValueError as exc:
        msg = str(exc)
        if "duplicates" in msg.lower():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=msg) from exc
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg) from exc

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Memory '{memory_id}' not found.",
        )
    return updated


@router.patch("/{memory_id}/disable", response_model=MemoryResponse, status_code=status.HTTP_200_OK)
@router.post("/{memory_id}/disable", response_model=MemoryResponse, status_code=status.HTTP_200_OK)
async def disable_single_memory(
    memory_id: UUID,
    request: Request,
    uid: UUID = Depends(current_user_id),
) -> dict[str, Any]:
    """Disable a memory so it is excluded from future LLM prompts."""
    pool = _get_pool(request)
    updated = await set_memory_active_status(pool, memory_id, uid, is_active=False)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Memory '{memory_id}' not found.",
        )
    return updated


@router.patch("/{memory_id}/enable", response_model=MemoryResponse, status_code=status.HTTP_200_OK)
@router.post("/{memory_id}/enable", response_model=MemoryResponse, status_code=status.HTTP_200_OK)
async def enable_single_memory(
    memory_id: UUID,
    request: Request,
    uid: UUID = Depends(current_user_id),
) -> dict[str, Any]:
    """Enable a previously disabled memory."""
    pool = _get_pool(request)
    updated = await set_memory_active_status(pool, memory_id, uid, is_active=True)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Memory '{memory_id}' not found.",
        )
    return updated


@router.delete("/{memory_id}", status_code=status.HTTP_200_OK)
async def delete_single_memory(
    memory_id: UUID,
    request: Request,
    uid: UUID = Depends(current_user_id),
) -> dict[str, Any]:
    """Permanently delete a memory record."""
    pool = _get_pool(request)
    deleted = await delete_memory(pool, memory_id, uid)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Memory '{memory_id}' not found.",
        )
    return {"deleted": True, "id": str(memory_id)}
