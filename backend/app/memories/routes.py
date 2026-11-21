"""Public and internal Memory API routes — S3-BE4.

Public routes:
- GET    /memory               — list memories (paginated), optionally ?map_id=
- GET    /memory/export        — GDPR export bundle of all user memories
- GET    /memory/{id}          — single memory (ownership enforced)
- PATCH  /memory/{id}          — edit content / enable / disable
- DELETE /memory/{id}          — hard delete

Worker-proposal write path:
- POST   /internal/memory/propose — validates, dedupes, enforces per-user limit, stores.

All routes require the requesting user to be the owner.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from core.supabase import current_user_id
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from memories.schemas import (
    GDPRExportResponse,
    MemoryItem,
    MemoryListResponse,
    MemoryPatchRequest,
    MemoryProposeRequest,
    MemoryProposeResponse,
)
from memories.service import delete_memory, gdpr_export, get_memory, list_memories, patch_memory, propose_memory

from db.repository import assert_map_owner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory", tags=["Memory (S3-BE4)"])
memories_plural_router = APIRouter(prefix="/memories", tags=["Memories (S3-BE4 Alias)"])
internal_router = APIRouter(prefix="/internal/memory", tags=["Internal Memory (S3-BE4)"])


def _pool(request: Request):
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database pool not available.",
        )
    return pool


# ---------------------------------------------------------------------------
# Worker-proposal write path: POST /internal/memory/propose
# ---------------------------------------------------------------------------


@internal_router.post(
    "/propose",
    response_model=MemoryProposeResponse,
    summary="Worker proposal write path (validate, dedupe, limit, store)",
)
async def propose_memory_route(
    payload: MemoryProposeRequest,
    request: Request,
    response: Response,
) -> MemoryProposeResponse:
    """Worker write-path to propose and persist an agent memory with deduplication and limits."""
    pool = _pool(request)

    # If caller provided a user auth token, verify it matches payload.user_id
    auth_hdr = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth_hdr and auth_hdr.lower().startswith("bearer "):
        token = auth_hdr[7:].strip()
        try:
            from core.supabase import anon_client

            user_res = anon_client().auth.get_user(token)
            if user_res and user_res.user and user_res.user.id and str(user_res.user.id) != str(payload.user_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Cannot propose memories for another user.",
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.debug("Caller token inspection skipped/failed: %s", exc)

    try:
        res = await propose_memory(pool, payload)
        if res.stored:
            response.status_code = status.HTTP_201_CREATED
        else:
            response.status_code = status.HTTP_200_OK
        return res
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GDPR Export Endpoint (Must be declared before /{memory_id} to avoid clash)
# ---------------------------------------------------------------------------


@router.get(
    "/export",
    response_model=GDPRExportResponse,
    summary="GDPR export of all user memories (Art. 15/20)",
)
@memories_plural_router.get(
    "/export",
    response_model=GDPRExportResponse,
    summary="GDPR export of all user memories (Art. 15/20)",
    include_in_schema=False,
)
async def export_memories(
    request: Request,
    uid: UUID = Depends(current_user_id),
) -> dict[str, Any]:
    """Return a JSON bundle of every memory record owned by the authenticated user."""
    return await gdpr_export(_pool(request), user_id=uid)


# ---------------------------------------------------------------------------
# Collection endpoints: GET /memory & GET /memory?map_id=...
# ---------------------------------------------------------------------------


@router.get("", response_model=MemoryListResponse, summary="List memories (paginated, optional map-scoped)")
@memories_plural_router.get("", response_model=MemoryListResponse, include_in_schema=False)
async def get_memories(
    request: Request,
    map_id: UUID | None = Query(None, description="Scope to a specific map"),
    is_active: bool | None = Query(None, description="Filter by active state"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    uid: UUID = Depends(current_user_id),
) -> dict[str, Any]:
    """List memories for the authenticated user, with optional map-scoping and pagination."""
    pool = _pool(request)

    if map_id is not None:
        is_owner = await assert_map_owner(pool, map_id, uid)
        if not is_owner:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Map not found.")

    items, total = await list_memories(
        pool,
        user_id=uid,
        map_id=map_id,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


# ---------------------------------------------------------------------------
# Single memory endpoints: GET / PATCH / DELETE
# ---------------------------------------------------------------------------


@router.get("/{memory_id}", response_model=MemoryItem, summary="Get single memory")
@memories_plural_router.get("/{memory_id}", response_model=MemoryItem, include_in_schema=False)
async def get_single_memory(
    memory_id: UUID,
    request: Request,
    uid: UUID = Depends(current_user_id),
) -> dict[str, Any]:
    mem = await get_memory(_pool(request), memory_id=memory_id, user_id=uid)
    if mem is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Memory '{memory_id}' not found.")
    return mem


@router.patch("/{memory_id}", response_model=MemoryItem, summary="Edit content or enable/disable")
@memories_plural_router.patch("/{memory_id}", response_model=MemoryItem, include_in_schema=False)
async def patch_single_memory(
    memory_id: UUID,
    payload: MemoryPatchRequest,
    request: Request,
    uid: UUID = Depends(current_user_id),
) -> dict[str, Any]:
    """Edit memory content, type, or active state. Ownership enforced by SQL WHERE user_id clause."""
    updated = await patch_memory(_pool(request), memory_id=memory_id, user_id=uid, payload=payload)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Memory '{memory_id}' not found.")
    return updated


@router.delete("/{memory_id}", status_code=status.HTTP_200_OK, summary="Delete a memory")
@memories_plural_router.delete("/{memory_id}", status_code=status.HTTP_200_OK, include_in_schema=False)
async def delete_single_memory(
    memory_id: UUID,
    request: Request,
    uid: UUID = Depends(current_user_id),
) -> dict[str, Any]:
    """Permanently delete a memory record owned by the authenticated user."""
    deleted = await delete_memory(_pool(request), memory_id=memory_id, user_id=uid)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Memory '{memory_id}' not found.")
    return {"deleted": True, "id": str(memory_id)}
