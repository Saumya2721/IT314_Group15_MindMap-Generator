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

from typing import Annotated, Any
from uuid import UUID

from asyncpg import UniqueViolationError
from auth.dependencies import require_internal_service
from core.supabase import get_current_user
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from memories.schemas import (
    GDPRExportResponse,
    MemoryItem,
    MemoryListResponse,
    MemoryPatchRequest,
    MemoryProposeRequest,
    MemoryProposeResponse,
)
from memories.service import delete_memory, gdpr_export, get_memory, list_memories, patch_memory, propose_memory
from routes.internal import verify_callback_auth
from services.rate_limiter import get_redis_pool, rate_limiter

from db.repository import assert_map_owner

router = APIRouter(prefix="/memory", tags=["Memory (S3-BE4)"])
memories_plural_router = APIRouter(prefix="/memories", tags=["Memories (S3-BE4 Alias)"])
internal_router = APIRouter(
    prefix="/internal/memory",
    tags=["Internal Memory (S3-BE4)"],
    dependencies=[Depends(require_internal_service)],
)


def _pool(request: Request):
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database pool not available.",
        )
    return pool


def memory_owner(request: Request, user=Depends(get_current_user)) -> UUID:
    if request.state.profile["role"] == "admin":
        raise HTTPException(403, "User memories are unavailable to administrators.")
    return UUID(str(user.id))


async def worker_memory_owner(
    request: Request, job_id: Annotated[UUID, Header(alias="X-Internal-Job-ID")]
) -> tuple[UUID, UUID]:
    job = await verify_callback_auth(job_id, request)
    pool = _pool(request)
    map_id = job["map_id"]
    owner = await pool.fetchval(
        """
        SELECT m.user_id FROM public.mind_maps m
        JOIN public.profiles p ON p.id = m.user_id
        JOIN public.generation_jobs j ON j.map_id = m.id
        WHERE m.id = $1 AND j.id = $2 AND p.role = 'user' AND p.suspended_at IS NULL
          AND (j.completed_at IS NULL OR j.completed_at > now() - interval '15 minutes')
        """,
        map_id,
        job_id,
    )
    if owner is None or job["status"] in ("failed", "rejected"):
        raise HTTPException(403, "Job cannot access user memories.")
    return owner, map_id


# ---------------------------------------------------------------------------
# Worker-proposal write path: POST /internal/memory/propose & GET /internal/memory
# ---------------------------------------------------------------------------


@internal_router.get(
    "",
    response_model=MemoryListResponse,
    summary="Worker internal memory retrieval (by user_id)",
)
async def internal_list_memories(
    request: Request,
    user_id: UUID = Query(..., description="Target user ID"),
    map_id: UUID | None = Query(None, description="Optional map scope"),
    owner: tuple[UUID, UUID] = Depends(worker_memory_owner),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """Internal read-path for workers to retrieve active memories for a given user."""
    if user_id != owner[0] or map_id != owner[1]:
        raise HTTPException(403, "Memory scope does not match the job.")
    pool = _pool(request)
    items, total = await list_memories(
        pool,
        user_id=user_id,
        map_id=map_id,
        is_active=True,
        include_global=True,
        limit=limit,
        offset=0,
    )
    return {"items": items, "total": total, "limit": limit, "offset": 0}


@internal_router.post(
    "/propose",
    response_model=MemoryProposeResponse,
    summary="Worker proposal write path (validate, dedupe, limit, store)",
)
async def propose_memory_route(
    payload: MemoryProposeRequest,
    request: Request,
    response: Response,
    owner: tuple[UUID, UUID] = Depends(worker_memory_owner),
) -> MemoryProposeResponse:
    """Worker write-path to propose and persist an agent memory with deduplication and limits."""
    pool = _pool(request)

    if payload.user_id != owner[0] or payload.map_id not in (None, owner[1]):
        raise HTTPException(403, "Memory scope does not match the job.")
    await rate_limiter.check(f"user:{owner[0]}", api="memory_propose", redis=get_redis_pool(request))

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
    uid: UUID = Depends(memory_owner),
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
    include_global: bool = Query(True, description="When map_id is set, also include user global memories"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    uid: UUID = Depends(memory_owner),
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
        include_global=include_global,
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
    uid: UUID = Depends(memory_owner),
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
    uid: UUID = Depends(memory_owner),
) -> dict[str, Any]:
    """Edit memory content, type, or active state. Ownership enforced by SQL WHERE user_id clause."""
    try:
        updated = await patch_memory(_pool(request), memory_id=memory_id, user_id=uid, payload=payload)
    except UniqueViolationError as exc:
        raise HTTPException(409, "An active memory with this content already exists.") from exc
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Memory '{memory_id}' not found.")
    return updated


@router.delete("/{memory_id}", status_code=status.HTTP_200_OK, summary="Delete a memory")
@memories_plural_router.delete("/{memory_id}", status_code=status.HTTP_200_OK, include_in_schema=False)
async def delete_single_memory(
    memory_id: UUID,
    request: Request,
    uid: UUID = Depends(memory_owner),
) -> dict[str, Any]:
    """Permanently delete a memory record owned by the authenticated user."""
    deleted = await delete_memory(_pool(request), memory_id=memory_id, user_id=uid)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Memory '{memory_id}' not found.")
    return {"deleted": True, "id": str(memory_id)}
