"""Map and generation API routes.

Migrated cleanly from mm_backend.maps into backend.maps.routes.
Enhanced with server-side search, sorting, pagination, and strict OCC save integrity.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from contracts.models import FileRef, GenerationRequest, JobType
from core.config import settings
from core.supabase import admin, current_user_id
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from services.queue import enqueue_generation_job, get_redis_pool
from services.runner import limiter
from uploads.service import delete_map_storage_files, read_uploads, store_files

from db.connection import transaction
from db.repository import (
    assert_map_owner,
    create_generation_job,
    delete_map_snapshot,
    get_chat_messages,
    get_chat_thread_summary,
    get_map_snapshot,
    get_or_create_chat_thread,
    get_recent_chat_messages,
    has_active_job,
    insert_chat_message,
    list_map_snapshots,
    restore_map_graph_from_snapshot,
    search_maps,
    update_mind_map_occ,
)

try:
    from seed.normalizer import SeedSubmission
except ImportError:
    from dataclasses import dataclass

    @dataclass
    class _SeedSubmissionFallback:
        topic: str | None
        ideas: str | None
        files: list

    SeedSubmission = _SeedSubmissionFallback  # type: ignore[misc]

logger = logging.getLogger(__name__)


def _build_etag(updated_at: datetime) -> str:
    """Format a consistent ETag header string from updated_at."""
    return f'"{updated_at.isoformat()}"'


def _parse_version_token(raw_token: str) -> datetime:
    """
    Parse an ETag or version string into a timezone-aware UTC datetime.
    Supports RFC standard ETags (e.g. '"2026-11-12T10:45:00.123456+00:00"', 'W/"..."'),
    ISO 8601 strings, and unix timestamps.
    """
    token = raw_token.strip()
    if token.startswith("W/"):
        token = token[2:]
    token = token.strip('"').strip("'").strip()

    if token.endswith("Z"):
        token = token[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(token)
    except ValueError:
        try:
            ts = float(token)
            dt = datetime.fromtimestamp(ts, tz=UTC)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid version / ETag format: '{raw_token}'. Expected ISO 8601 timestamp.",
            )

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


async def _build_memory_context_payload(conn, map_id: UUID) -> dict[str, Any] | None:
    """Helper to assemble short-term memory context (recent window + rolling summary) for worker dispatch."""
    try:
        from memory.service import MemoryService

        summary = await get_chat_thread_summary(conn, map_id)
        recent_msgs = await get_recent_chat_messages(conn, map_id, limit=6)
        if recent_msgs or summary:
            service = MemoryService()
            assembled = await service.build_context(
                str(map_id),
                recent_msgs,
                existing_summary=summary or "",
                window_size=6,
            )
            return assembled.model_dump()
    except Exception as exc:
        logger.debug("Memory context assembly skipped for map %s: %s", map_id, exc)
    return None


router = APIRouter(prefix="/api", tags=["Maps & generation"])

JOB_COLS = "id, map_id, job_type, status, progress_percent, error_message, created_at, started_at, completed_at"


class DetailLevel(StrEnum):
    overview = "overview"
    balanced = "balanced"
    detailed = "detailed"


class CreateMap(BaseModel):
    title: str = Field("Untitled mind map", min_length=1, max_length=255)
    description: str | None = Field(None, max_length=10000)


class UpdateMap(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=10000)
    tags: list[str] | None = None
    theme: str | None = None
    is_public: bool | None = None
    version: str | datetime | None = Field(
        None,
        description="Version/ETag for optimistic concurrency control (alternative to If-Match header)",
    )


class PaginatedMapsResponse(BaseModel):
    items: list[dict[str, Any]]
    total: int
    limit: int
    offset: int


class RegenerateRequest(BaseModel):
    instruction: str = Field(..., min_length=3, max_length=2000)
    detail_level: DetailLevel | None = None


class QuickGenerateRequest(BaseModel):
    topic: str | None = None
    ideas: str | None = None
    detail_level: DetailLevel | None = DetailLevel.overview
    map_id: UUID | None = None


def pool_of(request: Request):
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail="Database connection pool is unavailable. Please verify DATABASE_URL and Supabase project connectivity.",
        )
    return pool


def job_dict(row) -> dict:
    d = dict(row)
    if d.get("error_message"):
        d["error_message"] = d["error_message"][:500]
    return d


# ------------------------------------------------------------------ maps CRUD
@router.post("/maps", status_code=201)
async def create_map(body: CreateMap, request: Request, response: Response, uid: UUID = Depends(current_user_id)):
    row = await pool_of(request).fetchrow(
        """INSERT INTO public.mind_maps (user_id, title, description)
           VALUES ($1, $2, $3)
           RETURNING id, title, description, is_public, created_at, updated_at""",
        uid,
        body.title.strip(),
        body.description,
    )
    result = dict(row)
    etag = _build_etag(row["updated_at"])
    response.headers["ETag"] = etag
    result["etag"] = row["updated_at"].isoformat()
    return result


@router.get("/maps", response_model=PaginatedMapsResponse)
async def list_maps(
    request: Request,
    response: Response,
    q: str | None = Query(None, description="Search query for titles, descriptions, and node labels"),
    sort: str = Query(
        "updated_at_desc",
        description="Sorting option: updated_at_desc, updated_at_asc, created_at_desc, created_at_asc, title_asc, title_desc",
    ),
    limit: int = Query(20, ge=1, le=100, description="Number of results per page"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    uid: UUID = Depends(current_user_id),
):
    """
    Search and list maps owned by the current user:
    - Queries titles, descriptions, and node labels via database search_maps
    - Supports sorting by updated_at, created_at, title
    - Paginated with limit and offset
    """
    pool = pool_of(request)
    items, total = await search_maps(
        pool,
        user_id=uid,
        query=q,
        sort=sort,
        limit=limit,
        offset=offset,
    )

    # Attach etag to each item
    for item in items:
        if "updated_at" in item and item["updated_at"]:
            item["etag"] = item["updated_at"].isoformat()

    response.headers["X-Total-Count"] = str(total)
    return PaginatedMapsResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/maps/{map_id}", status_code=200)
async def get_map(
    map_id: UUID,
    request: Request,
    response: Response,
    uid: UUID = Depends(current_user_id),
):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")
    row = await pool.fetchrow(
        "SELECT id, title, description, is_public, created_at, updated_at FROM public.mind_maps WHERE id = $1",
        map_id,
    )
    if not row:
        raise HTTPException(404, "Map not found")

    result = dict(row)
    etag = _build_etag(row["updated_at"])
    response.headers["ETag"] = etag
    result["etag"] = row["updated_at"].isoformat()
    return result


@router.patch("/maps/{map_id}", status_code=200)
async def update_map(
    map_id: UUID,
    body: UpdateMap,
    request: Request,
    response: Response,
    if_match: str | None = Header(None, description="Current map ETag for optimistic concurrency"),
    uid: UUID = Depends(current_user_id),
):
    """
    Update map metadata (title, description, tags, theme, is_public).
    Strict save-state semantics:
      - Uses optimistic concurrency control (OCC).
      - Version token must be provided via `If-Match` header or `version` in request body.
      - Returns HTTP 428 if version token is missing.
      - Returns clean HTTP 409 Conflict if concurrent modifications occurred.
      - Returns clean HTTP 500 on database error so client never shows failed save as saved.
    """
    # 1. Enforce mandatory optimistic concurrency token
    raw_version = if_match or (str(body.version) if body.version is not None else None)
    if not raw_version:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail="Precondition Required: 'If-Match' header or 'version' field in body is required for optimistic concurrency.",
        )

    expected_updated_at = _parse_version_token(raw_version)

    # 2. Check at least one field is being updated
    has_changes = any(x is not None for x in (body.title, body.description, body.tags, body.theme, body.is_public))
    if not has_changes:
        raise HTTPException(status_code=400, detail="No fields to update")

    pool = pool_of(request)

    # 3. Perform atomic update with OCC
    try:
        updated_row, conflict_status = await update_mind_map_occ(
            pool,
            map_id=map_id,
            user_id=uid,
            expected_updated_at=expected_updated_at,
            title=body.title,
            description=body.description,
            is_public=body.is_public,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Database error while updating map %s: %s", map_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update map due to a database error. Please retry.",
        ) from exc

    if conflict_status == "NOT_FOUND":
        raise HTTPException(status_code=404, detail="Map not found")

    if conflict_status == "CONFLICT":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conflict: Map has been modified by another session. Please reload to see the latest version.",
        )

    if not updated_row:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Update failed to persist. Please retry.",
        )

    # 4. Attach new ETag & return updated map
    etag = _build_etag(updated_row["updated_at"])
    response.headers["ETag"] = etag
    updated_row["etag"] = updated_row["updated_at"].isoformat()

    # Pass through tags and theme if provided by client
    if body.tags is not None:
        updated_row["tags"] = body.tags
    if body.theme is not None:
        updated_row["theme"] = body.theme

    return updated_row


# ------------------------------------------------------------------ Direct Quick Generate
@router.post("/maps/quick-generate", status_code=200)
async def quick_generate(
    body: QuickGenerateRequest,
    request: Request,
    uid: UUID = Depends(current_user_id),
):
    """Synchronous generation endpoint routed through the canonical ARQ worker queue."""
    pool = pool_of(request)
    redis_pool = get_redis_pool(request)
    if not redis_pool:
        raise HTTPException(503, "Generation queue service is unavailable. Redis connection is required.")

    if not (body.topic or "").strip() and not (body.ideas or "").strip():
        raise HTTPException(422, "Provide a topic or some ideas.")

    limiter.check(uid)

    map_id = body.map_id
    if map_id:
        is_owner = await assert_map_owner(pool, map_id, uid)
        if not is_owner:
            raise HTTPException(404, "Map not found")
    else:
        map_title = (body.topic or "Untitled Mind Map").strip()[:100]
        row = await pool.fetchrow(
            "INSERT INTO public.mind_maps (user_id, title) VALUES ($1, $2) RETURNING id",
            uid,
            map_title,
        )
        map_id = row["id"]

    if await has_active_job(pool, map_id):
        raise HTTPException(409, "Generation already in progress for this map.")

    job_id = uuid4()
    callback_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(callback_token.encode("utf-8")).hexdigest()

    summary_msg = f"Quick generate mind map for topic: '{body.topic or 'General'}'"
    async with transaction(pool) as conn:
        thread_id = await get_or_create_chat_thread(conn, map_id, uid)
        msg_id = await insert_chat_message(conn, thread_id, "user", summary_msg)
        await create_generation_job(
            conn,
            map_id=map_id,
            job_type=JobType.GENERATE,
            job_id=job_id,
            triggered_by_message_id=msg_id,
            callback_token_hash=token_hash,
        )

    mem_ctx = await _build_memory_context_payload(pool, map_id)
    gen_req = GenerationRequest(
        job_id=job_id,
        map_id=map_id,
        job_type=JobType.GENERATE,
        topic=body.topic,
        ideas=body.ideas,
        files=[],
        detail_level=body.detail_level.value if body.detail_level else "overview",
        callback_base_url=settings.BACKEND_INTERNAL_URL,
        callback_token=callback_token,
        memory_context=mem_ctx,
    )
    try:
        enqueued_id = await enqueue_generation_job(redis_pool, gen_req.model_dump(mode="json"))
        if not enqueued_id:
            raise RuntimeError("Queue declined job dispatch")
    except Exception as exc:
        logger.exception("Failed to enqueue quick generation job %s for map %s: %s", job_id, map_id, exc)
        try:
            await pool.execute(
                """
                UPDATE public.generation_jobs
                   SET status = 'rejected',
                       completed_at = now(),
                       progress_percent = 0,
                       error_message = $2
                 WHERE id = $1 AND started_at IS NULL
                """,
                job_id,
                "Failed to queue generation job. Please try again.",
            )
        except Exception as db_exc:
            logger.warning("Failed to mark job %s rejected during compensation: %s", job_id, db_exc)
        raise HTTPException(503, "Failed to enqueue generation job.")

    # Await worker execution up to 60s
    poll_start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - poll_start < 60.0:
        job_row = await pool.fetchrow(
            "SELECT status, error_message FROM public.generation_jobs WHERE id = $1",
            job_id,
        )
        if job_row and job_row["status"] == "completed":
            map_row = await pool.fetchrow(
                "SELECT id, title, description, is_public, created_at, updated_at FROM public.mind_maps WHERE id = $1",
                map_id,
            )
            nodes = await pool.fetch("SELECT * FROM public.nodes WHERE map_id = $1 ORDER BY created_at ASC", map_id)
            edges = await pool.fetch("SELECT * FROM public.edges WHERE map_id = $1", map_id)
            return {
                "map": dict(map_row),
                "nodes": [dict(n) for n in nodes],
                "edges": [dict(e) for e in edges],
                "job_id": str(job_id),
            }
        if job_row and job_row["status"] in ("failed", "rejected"):
            err = job_row["error_message"] or "Generation failed unexpectedly."
            raise HTTPException(502, f"Generation failed: {err}")
        await asyncio.sleep(0.3)

    return {
        "status": "accepted",
        "map_id": map_id,
        "job_id": job_id,
        "poll": f"/api/maps/{map_id}/jobs/{job_id}",
    }


# ------------------------------------------------------------------ Submit Seed & Generate
@router.post("/maps/{map_id}/generate", status_code=202)
async def generate(
    map_id: UUID,
    request: Request,
    background: BackgroundTasks,
    topic: str | None = Form(None),
    ideas: str | None = Form(None),
    detail_level: DetailLevel | None = Form(None),
    file: UploadFile | str | None = File(None, description="Upload a single PDF or image"),
    files: list[UploadFile | str] | UploadFile | str | None = File(None, description="Or upload multiple files"),
    uid: UUID = Depends(current_user_id),
):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")

    topic = (topic or "").strip() or None
    ideas = (ideas or "").strip() or None
    combined_files = []

    def _is_file(obj: Any) -> bool:
        return (
            obj is not None
            and hasattr(obj, "filename")
            and hasattr(obj, "read")
            and bool(getattr(obj, "filename", None))
        )

    if _is_file(file):
        combined_files.append(file)
    if files:
        if isinstance(files, list):
            for f in files:
                if _is_file(f):
                    combined_files.append(f)
        elif _is_file(files):
            combined_files.append(files)

    has_files = bool(combined_files)
    if not (topic or ideas or has_files):
        raise HTTPException(422, "Provide a topic, some ideas, or at least one file.")
    if topic and len(topic) > 500:
        raise HTTPException(422, "Topic is too long (max 500 characters).")
    if ideas and len(ideas) > 20000:
        raise HTTPException(422, "Ideas text is too long (max 20000 characters).")

    if await pool.fetchval("SELECT 1 FROM public.nodes WHERE map_id = $1 LIMIT 1", map_id):
        raise HTTPException(409, "This map already has content. Use regenerate instead.")
    if await has_active_job(pool, map_id):
        raise HTTPException(409, "Generation already in progress for this map.")

    limiter.check(uid)

    job_id = uuid4()
    callback_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(callback_token.encode("utf-8")).hexdigest()

    summary_msg = f"Generate mind map for topic: '{topic or 'General'}' (Detail: {detail_level.value if detail_level else 'overview'})"
    async with transaction(pool) as conn:
        thread_id = await get_or_create_chat_thread(conn, map_id, uid)
        msg_id = await insert_chat_message(conn, thread_id, "user", summary_msg)
        await create_generation_job(
            conn,
            map_id=map_id,
            job_type=JobType.GENERATE,
            job_id=job_id,
            triggered_by_message_id=msg_id,
            callback_token_hash=token_hash,
        )

    stored_keys: list[str] = []
    try:
        uploads = []
        if combined_files:
            items = await read_uploads(combined_files)  # type: ignore[arg-type]
            uploads = await store_files(uid, map_id, job_id, items)
            stored_keys = [u.storage_key for u in uploads if getattr(u, "storage_key", None)]

        redis_pool = get_redis_pool(request)
        if not redis_pool:
            raise HTTPException(503, "Generation queue service is unavailable. Redis connection is required.")

        file_refs = [
            FileRef(
                file_name=u.file_name,
                mime_type=u.mime_type,
                url=getattr(u, "signed_url", None) or "",
                storage_key=getattr(u, "storage_key", None) or f"{uid}/{map_id}/{job_id}/0_{u.file_name}",
            )
            for u in uploads
        ]
        mem_ctx = await _build_memory_context_payload(pool, map_id)
        gen_req = GenerationRequest(
            job_id=job_id,
            map_id=map_id,
            job_type=JobType.GENERATE,
            topic=topic,
            ideas=ideas,
            files=file_refs,
            detail_level=detail_level.value if detail_level else None,
            callback_base_url=settings.BACKEND_INTERNAL_URL,
            callback_token=callback_token,
            memory_context=mem_ctx,
        )
        enqueued_id = await enqueue_generation_job(redis_pool, gen_req.model_dump(mode="json"))
        if not enqueued_id:
            raise RuntimeError("Redis declined job enqueue")
    except HTTPException as exc:
        logger.exception("Failed during file storage or queue dispatch for map %s, job %s: %s", map_id, job_id, exc)
        if stored_keys:
            try:
                await asyncio.to_thread(admin.storage.from_("seed-assets").remove, stored_keys)
            except Exception as rm_exc:
                logger.warning("Failed to remove storage keys %s during compensation: %s", stored_keys, rm_exc)
        try:
            await pool.execute(
                """
                UPDATE public.generation_jobs
                   SET status = 'rejected',
                       completed_at = now(),
                       progress_percent = 0,
                       error_message = $2
                 WHERE id = $1 AND started_at IS NULL
                """,
                job_id,
                f"Failed to queue generation job: {exc.detail}",
            )
        except Exception as db_exc:
            logger.warning("Failed to mark job %s rejected during compensation: %s", job_id, db_exc)
        raise exc
    except Exception as exc:
        logger.exception("Failed during file storage or queue dispatch for map %s, job %s: %s", map_id, job_id, exc)
        if stored_keys:
            try:
                await asyncio.to_thread(admin.storage.from_("seed-assets").remove, stored_keys)
            except Exception as rm_exc:
                logger.warning("Failed to remove storage keys %s during compensation: %s", stored_keys, rm_exc)
        try:
            await pool.execute(
                """
                UPDATE public.generation_jobs
                   SET status = 'rejected',
                       completed_at = now(),
                       progress_percent = 0,
                       error_message = $2
                 WHERE id = $1 AND started_at IS NULL
                """,
                job_id,
                "Failed to queue generation job. Please try again.",
            )
        except Exception as db_exc:
            logger.warning("Failed to mark job %s rejected during compensation: %s", job_id, db_exc)
        raise HTTPException(502, "Failed to initiate generation job. Please try again.")

    return {
        "status": "accepted",
        "map_id": map_id,
        "job_id": job_id,
        "poll": f"/api/maps/{map_id}/jobs/{job_id}",
    }


# ------------------------------------------------------------------ Regenerate Whole Map
@router.post("/maps/{map_id}/regenerate", status_code=202)
async def regenerate(
    map_id: UUID,
    body: RegenerateRequest,
    request: Request,
    background: BackgroundTasks,
    uid: UUID = Depends(current_user_id),
):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")

    if await has_active_job(pool, map_id):
        raise HTTPException(409, "Generation already in progress for this map.")
    has_seed = await pool.fetchval(
        """SELECT 1 FROM public.seed_assets a JOIN public.generation_jobs j ON j.id = a.generation_job_id
            WHERE j.map_id = $1 AND a.content_text IS NOT NULL LIMIT 1""",
        map_id,
    )
    if not has_seed:
        raise HTTPException(409, "This map has no original seed to regenerate from. Run generate first.")

    limiter.check(uid)

    job_id = uuid4()
    callback_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(callback_token.encode("utf-8")).hexdigest()

    summary_msg = f"Regenerate mind map with instruction: '{body.instruction.strip()}'"
    async with transaction(pool) as conn:
        thread_id = await get_or_create_chat_thread(conn, map_id, uid)
        msg_id = await insert_chat_message(conn, thread_id, "user", summary_msg)
        await create_generation_job(
            conn,
            map_id=map_id,
            job_type=JobType.REGENERATE_FULL,
            job_id=job_id,
            triggered_by_message_id=msg_id,
            callback_token_hash=token_hash,
        )

    try:
        redis_pool = get_redis_pool(request)
        if not redis_pool:
            raise HTTPException(503, "Generation queue service is unavailable. Redis connection is required.")

        mem_ctx = await _build_memory_context_payload(pool, map_id)
        gen_req = GenerationRequest(
            job_id=job_id,
            map_id=map_id,
            job_type=JobType.REGENERATE_FULL,
            regeneration_instruction=body.instruction.strip(),
            detail_level=body.detail_level.value if body.detail_level else None,
            callback_base_url=settings.BACKEND_INTERNAL_URL,
            callback_token=callback_token,
            memory_context=mem_ctx,
        )
        enqueued_id = await enqueue_generation_job(redis_pool, gen_req.model_dump(mode="json"))
        if not enqueued_id:
            raise RuntimeError("Redis declined job enqueue")
    except HTTPException as exc:
        logger.exception("Failed during queue dispatch for map %s, job %s: %s", map_id, job_id, exc)
        try:
            await pool.execute(
                """
                UPDATE public.generation_jobs
                   SET status = 'rejected',
                       completed_at = now(),
                       progress_percent = 0,
                       error_message = $2
                 WHERE id = $1 AND started_at IS NULL
                """,
                job_id,
                f"Failed to queue regeneration job: {exc.detail}",
            )
        except Exception as db_exc:
            logger.warning("Failed to mark job %s rejected during compensation: %s", job_id, db_exc)
        raise exc
    except Exception as exc:
        logger.exception("Failed during queue dispatch for map %s, job %s: %s", map_id, job_id, exc)
        try:
            await pool.execute(
                """
                UPDATE public.generation_jobs
                   SET status = 'rejected',
                       completed_at = now(),
                       progress_percent = 0,
                       error_message = $2
                 WHERE id = $1 AND started_at IS NULL
                """,
                job_id,
                "Failed to queue regeneration job. Please try again.",
            )
        except Exception as db_exc:
            logger.warning("Failed to mark job %s rejected during compensation: %s", job_id, db_exc)
        raise HTTPException(502, "Failed to initiate regeneration job. Please try again.")

    return {
        "status": "accepted",
        "map_id": map_id,
        "job_id": job_id,
        "poll": f"/api/maps/{map_id}/jobs/{job_id}",
    }


# ------------------------------------------------------------------ Reject Node Regeneration (501)
@router.post("/maps/{map_id}/nodes/{node_id}/regenerate", status_code=501)
async def regenerate_node(
    map_id: UUID,
    node_id: UUID,
    request: Request,
    uid: UUID = Depends(current_user_id),
):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")
    node_exists = await pool.fetchval("SELECT 1 FROM public.nodes WHERE id = $1 AND map_id = $2", node_id, map_id)
    if not node_exists:
        raise HTTPException(404, "Node not found on this map")

    raise HTTPException(
        status_code=501,
        detail="Node-level regeneration is not yet supported. Please use full map regeneration.",
    )


# ------------------------------------------------------------------ Polling & Job inspection
@router.get("/maps/{map_id}/jobs/latest")
async def latest_job(map_id: UUID, request: Request, uid: UUID = Depends(current_user_id)):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")
    row = await pool.fetchrow(
        f"SELECT {JOB_COLS} FROM public.generation_jobs WHERE map_id = $1 ORDER BY created_at DESC LIMIT 1",
        map_id,
    )
    return {"job": job_dict(row) if row else None}


@router.get("/maps/{map_id}/jobs/{job_id}")
async def get_job(map_id: UUID, job_id: UUID, request: Request, uid: UUID = Depends(current_user_id)):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")
    row = await pool.fetchrow(
        f"SELECT {JOB_COLS} FROM public.generation_jobs WHERE id = $1 AND map_id = $2",
        job_id,
        map_id,
    )
    if not row:
        raise HTTPException(404, "Job not found")
    return job_dict(row)


@router.get("/jobs/{job_id}")
async def get_job_by_id(job_id: UUID, request: Request, uid: UUID = Depends(current_user_id)):
    pool = pool_of(request)
    row = await pool.fetchrow(
        f"SELECT {JOB_COLS} FROM public.generation_jobs WHERE id = $1",
        job_id,
    )
    if not row:
        raise HTTPException(404, "Job not found")
    await assert_map_owner(pool, row["map_id"], uid)
    return job_dict(row)


@router.get("/maps/{map_id}/chat")
async def get_chat(map_id: UUID, request: Request, uid: UUID = Depends(current_user_id)):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")
    return await get_chat_messages(pool, map_id)


@router.get("/maps/{map_id}/graph")
async def get_graph(map_id: UUID, request: Request, uid: UUID = Depends(current_user_id)):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")
    nodes = await pool.fetch(
        """SELECT id, label, description, pos_x, pos_y, background_color, text_color,
                  width, height, node_type, node_data
             FROM public.nodes WHERE map_id = $1""",
        map_id,
    )
    edges = await pool.fetch(
        "SELECT id, source_node_id, target_node_id, label, edge_data FROM public.edges WHERE map_id = $1",
        map_id,
    )
    return {"nodes": [dict(n) for n in nodes], "edges": [dict(e) for e in edges]}


# ------------------------------------------------------------------
# Version History / Snapshots (US-04 / S2-FE7)
# ------------------------------------------------------------------


@router.get("/maps/{map_id}/snapshots", status_code=200)
async def list_snapshots(map_id: UUID, request: Request, uid: UUID = Depends(current_user_id)):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")
    return await list_map_snapshots(pool, map_id)


@router.get("/maps/{map_id}/snapshots/{snapshot_id}", status_code=200)
async def get_snapshot(map_id: UUID, snapshot_id: UUID, request: Request, uid: UUID = Depends(current_user_id)):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")
    snapshot = await get_map_snapshot(pool, map_id, snapshot_id)
    if not snapshot:
        raise HTTPException(404, "Snapshot not found")
    return snapshot


@router.post("/maps/{map_id}/snapshots/{snapshot_id}/restore", status_code=200)
async def restore_snapshot(
    map_id: UUID,
    snapshot_id: UUID,
    request: Request,
    uid: UUID = Depends(current_user_id),
):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")

    snapshot_data = await restore_map_graph_from_snapshot(pool, map_id=map_id, snapshot_id=snapshot_id)
    if not snapshot_data:
        raise HTTPException(404, "Snapshot not found")
    return {"snapshot_id": str(snapshot_id), "snapshot_data": snapshot_data}


@router.delete("/maps/{map_id}/snapshots/{snapshot_id}", status_code=200)
async def delete_snapshot(
    map_id: UUID,
    snapshot_id: UUID,
    request: Request,
    uid: UUID = Depends(current_user_id),
):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")
    deleted = await delete_map_snapshot(pool, map_id=map_id, snapshot_id=snapshot_id)
    if not deleted:
        raise HTTPException(404, "Snapshot not found")
    return {"message": "Snapshot deleted successfully"}


@router.delete("/maps/{map_id}", status_code=200)
async def delete_map(map_id: UUID, request: Request, uid: UUID = Depends(current_user_id)):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")

    try:
        await delete_map_storage_files(user_id=uid, map_id=map_id, pool=pool)
    except Exception as exc:
        logger.error("Storage cleanup failed for map %s: %s", map_id, exc)
        raise HTTPException(
            status_code=502,
            detail="Failed to clean up associated storage files. Map deletion aborted.",
        ) from exc

    await pool.execute("DELETE FROM public.mind_maps WHERE id = $1", map_id)
    return {"message": "Map deleted successfully"}
