"""Map and generation API routes.

Migrated cleanly from mm_backend.maps into backend.maps.routes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
from collections import deque
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from contracts.models import DEFAULT_PURPOSE, FileRef, GenerationRequest, JobType
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
)
from maps.graph_validator import (
    check_edge_creates_cycle,
    find_root_node_id,
    get_node_descendant_ids,
    validate_graph_structure,
)
from maps.schemas import (
    BatchGraphUpdateRequest,
    BranchResponse,
    CommentCreate,
    CommentResponse,
    CommentUpdate,
    EdgeCreate,
    EdgeUpdate,
    NodeCreate,
    NodeRegenerateRequest,
    NodeUpdate,
    SnapshotCreate,
    SnapshotDetailResponse,
    SnapshotResponse,
    ValidateGraphRequest,
)
from pydantic import BaseModel, Field
from services.export_service import (
    ExportConversionError,
    build_full_graph_svg,
    get_export_adapter,
)
from services.queue import enqueue_generation_job, get_redis_pool
from services.runner import limiter
from uploads.service import (
    delete_map_storage_files,
    read_uploads,
    store_files,
    validate_purpose,
    validate_seed_url,
)

from db.connection import transaction
from db.repository import (
    OptimisticConcurrencyError,
    assert_map_owner,
    check_and_bump_map_version,
    create_generation_job,
    create_map_edge,
    create_map_node,
    create_map_snapshot,
    create_node_comment,
    delete_map_edge,
    delete_map_node,
    delete_map_snapshot,
    delete_node_comment,
    get_chat_messages,
    get_edge_by_id,
    get_map_snapshot,
    get_map_version,
    get_node_by_id,
    get_node_comment,
    get_or_create_chat_thread,
    has_active_job,
    has_active_node_job,
    insert_chat_message,
    list_map_edges,
    list_map_nodes,
    list_map_snapshots,
    list_node_comments,
    lock_map_for_mutation,
    search_maps,
    update_map_edge,
    update_map_node,
    update_node_comment,
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

router = APIRouter(prefix="/api", tags=["Maps & generation"])

JOB_COLS = "id, map_id, job_type, status, progress_percent, error_message, created_at, started_at, completed_at"


class DetailLevel(StrEnum):
    overview = "overview"
    balanced = "balanced"
    detailed = "detailed"


class CreateMap(BaseModel):
    title: str = Field("Untitled mind map", min_length=1, max_length=255)


class UpdateMap(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=10000)
    is_public: bool | None = None
    tags: list[str] | None = None
    theme: str | None = None
    view_x: float | None = Field(None, description="Canvas horizontal viewport coordinate")
    view_y: float | None = Field(None, description="Canvas vertical viewport coordinate")
    view_zoom: float | None = Field(None, gt=0, description="Canvas zoom level for the persisted viewport")
    expected_version: int | None = None
    version: str | datetime | None = None


class RegenerateRequest(BaseModel):
    instruction: str = Field(..., min_length=3, max_length=2000)
    detail_level: DetailLevel | None = None


class QuickGenerateRequest(BaseModel):
    seed_url: str | None = None
    topic: str | None = None
    ideas: str | None = None
    detail_level: DetailLevel | None = DetailLevel.overview
    purpose: str | None = Field(
        default=DEFAULT_PURPOSE,
        description="Map generation purpose: general, study, or research (S3-FE5)",
    )
    map_id: UUID | None = None


def _parse_version_token(token: str | None) -> datetime | None:
    if not token:
        return None
    cleaned = token.strip()
    if cleaned.startswith(('W/"', 'w/"')) and cleaned.endswith('"'):
        cleaned = cleaned[3:-1]
    elif cleaned.startswith('"') and cleaned.endswith('"'):
        cleaned = cleaned[1:-1]
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid version/ETag format: {token}",
        )
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


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
async def create_map(body: CreateMap, request: Request, uid: UUID = Depends(current_user_id)):
    row = await pool_of(request).fetchrow(
        "INSERT INTO public.mind_maps (user_id, title) VALUES ($1, $2) "
        "RETURNING id, title, is_public, created_at, updated_at",
        uid,
        body.title.strip(),
    )
    return dict(row)


@router.get("/maps")
async def list_maps(
    request: Request,
    response: Response,
    q: str | None = Query(
        None,
        max_length=255,
        description="Optional case-insensitive search term matched against map title and description",
    ),
    limit: int = Query(
        20,
        ge=1,
        le=100,
        description="Maximum number of results to return when searching",
    ),
    offset: int = Query(
        0,
        ge=0,
        description="Pagination offset",
    ),
    sort: str = Query(
        "updated_at_desc",
        description="Sorting order",
    ),
    uid: UUID = Depends(current_user_id),
):
    pool = pool_of(request)
    if q and q.strip():
        rows = await search_maps(pool, uid, q, limit=limit, offset=offset, sort=sort)
    else:
        sort_map = {
            "updated_at_desc": "m.updated_at DESC",
            "updated_at_asc": "m.updated_at ASC",
            "created_at_desc": "m.created_at DESC",
            "created_at_asc": "m.created_at ASC",
            "title_asc": "m.title ASC",
            "title_desc": "m.title DESC",
        }
        order_clause = sort_map.get(sort, "m.updated_at DESC")
        rows = await pool.fetch(
            f"""SELECT m.id, m.title, m.is_public, m.created_at, m.updated_at,
                      (SELECT count(*)::int FROM public.nodes n WHERE n.map_id = m.id) AS node_count,
                      (SELECT j.status FROM public.generation_jobs j WHERE j.map_id = m.id ORDER BY j.created_at DESC LIMIT 1) AS latest_job_status,
                      (SELECT j.progress_percent::int FROM public.generation_jobs j WHERE j.map_id = m.id ORDER BY j.created_at DESC LIMIT 1) AS latest_job_progress,
                      (SELECT j.id FROM public.generation_jobs j WHERE j.map_id = m.id ORDER BY j.created_at DESC LIMIT 1) AS latest_job_id
               FROM public.mind_maps m
               WHERE m.user_id = $1
               ORDER BY {order_clause}
               LIMIT $2 OFFSET $3""",
            uid,
            limit,
            offset,
        )
    return [dict(r) for r in rows]


@router.get("/maps/{map_id}", status_code=200)
async def get_map(map_id: UUID, request: Request, uid: UUID = Depends(current_user_id)):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")
    row = await pool.fetchrow(
        "SELECT id, title, is_public, created_at, updated_at, view_x, view_y, view_zoom FROM public.mind_maps WHERE id = $1",
        map_id,
    )
    if not row:
        raise HTTPException(404, "Map not found")
    return dict(row)


@router.patch("/maps/{map_id}", status_code=200)
async def update_map(
    map_id: UUID,
    body: UpdateMap,
    request: Request,
    response: Response,
    if_match: str | None = Header(None, alias="If-Match"),
    uid: UUID = Depends(current_user_id),
):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")

    updates = []
    args: list[Any] = [map_id]
    if body.title is not None and body.title.strip():
        args.append(body.title.strip())
        updates.append(f"title = ${len(args)}")
    if body.description is not None:
        args.append(body.description)
        updates.append(f"description = ${len(args)}")
    if body.is_public is not None:
        args.append(body.is_public)
        updates.append(f"is_public = ${len(args)}")
    if body.view_x is not None:
        args.append(body.view_x)
        updates.append(f"view_x = ${len(args)}")
    if body.view_y is not None:
        args.append(body.view_y)
        updates.append(f"view_y = ${len(args)}")
    if body.view_zoom is not None:
        args.append(body.view_zoom)
        updates.append(f"view_zoom = ${len(args)}")

    if not updates:
        raise HTTPException(400, "No fields to update")

    updates.append("updated_at = now()")
    sql = f"UPDATE public.mind_maps SET {', '.join(updates)} WHERE id = $1 RETURNING id, title, is_public, created_at, updated_at"

    # Atomic OCC check and row update under transaction
    async with transaction(pool) as conn:
        occ_token = if_match or (str(body.version) if body.version is not None else None)
        if occ_token is not None:
            curr = await conn.fetchrow("SELECT updated_at FROM public.mind_maps WHERE id = $1 FOR UPDATE", map_id)
            if curr and curr["updated_at"]:
                expected_dt = _parse_version_token(occ_token)
                if expected_dt is not None:
                    curr_dt = curr["updated_at"]
                    if isinstance(curr_dt, str):
                        curr_dt = _parse_version_token(curr_dt)
                    if curr_dt and abs((curr_dt - expected_dt).total_seconds()) > 0.001:
                        raise HTTPException(
                            status_code=409,
                            detail="Map has been modified by another operation. Please reload.",
                        )
        try:
            row = await conn.fetchrow(sql, *args)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Database update failed: {e}")

    if not row:
        raise HTTPException(404, "Map not found")

    result = dict(row)
    if body.description is not None:
        result["description"] = body.description
    if body.tags is not None:
        result["tags"] = body.tags
    if body.theme is not None:
        result["theme"] = body.theme
    if row.get("updated_at"):
        val = row["updated_at"]
        if hasattr(val, "isoformat"):
            val = val.isoformat()
        clean_val = str(val).strip('"')
        response.headers["ETag"] = f'"{clean_val}"'
    return result


# ------------------------------------------------------------------ Map Export (S2-BE6 / US-07)
@router.get("/maps/{map_id}/export")
async def export_map(
    map_id: UUID,
    request: Request,
    format: str = Query("png", description="Export format: 'pdf', 'png', or 'svg'"),
    scale: float = Query(2.0, ge=1.0, le=4.0, description="Export scale multiplier"),
    theme: str = Query("light", description="Export theme: 'light' or 'dark'"),
    uid: UUID = Depends(current_user_id),
):
    """Export the entire mind map layout to PDF, PNG, or SVG (S2-BE6 / US-07)."""
    fmt = format.lower().strip()
    if fmt not in ("pdf", "png", "svg"):
        raise HTTPException(
            status_code=422,
            detail="Invalid export format. Must be 'pdf', 'png', or 'svg'.",
        )

    thm = theme.lower().strip()
    if thm not in ("light", "dark"):
        raise HTTPException(
            status_code=422,
            detail="Invalid export theme. Must be 'light' or 'dark'.",
        )

    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(status_code=404, detail="Map not found")

    await limiter.check(uid, api="export", redis=get_redis_pool(request))

    map_row = await pool.fetchrow("SELECT title FROM public.mind_maps WHERE id = $1", map_id)
    raw_title = (map_row["title"] if map_row and map_row["title"] else "mindmap").strip()
    safe_title = "".join(c for c in raw_title if c.isalnum() or c in ("-", "_", " ")).strip() or "mindmap"

    nodes = await list_map_nodes(pool, map_id)
    edges = await list_map_edges(pool, map_id)

    svg_content = build_full_graph_svg(nodes, edges, theme=thm, scale=scale)

    if fmt == "svg":
        media_type = "image/svg+xml"
        data = svg_content.encode("utf-8")
    else:
        adapter = get_export_adapter()
        try:
            data = await adapter.convert(svg_content, fmt, scale=scale)
        except ExportConversionError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Export conversion service failed: {exc}",
            )
        media_type = "application/pdf" if fmt == "pdf" else "image/png"

    filename = f"{safe_title}.{fmt}"
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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

    seed_url = (body.seed_url or "").strip() or None
    if seed_url:
        valid_url, error = validate_seed_url(seed_url)
        if not valid_url:
            raise HTTPException(422, error)
    if not ((body.topic or "").strip() or (body.ideas or "").strip() or seed_url):
        raise HTTPException(422, "Provide a topic, some ideas, or a seed URL.")

    p_ok, norm_purpose, p_err = validate_purpose(body.purpose)
    if not p_ok:
        raise HTTPException(422, p_err)

    await limiter.check(uid, api="generation", redis=redis_pool)

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

    summary_msg = (
        f"Quick generate mind map for topic: '{body.topic or 'General'}' "
        f"(Detail: {body.detail_level.value if body.detail_level else 'overview'}, "
        f"Purpose: {norm_purpose})"
    )
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

    gen_req = GenerationRequest(
        job_id=job_id,
        map_id=map_id,
        job_type=JobType.GENERATE,
        user_id=uid,
        seed_url=seed_url,
        topic=body.topic,
        ideas=body.ideas,
        files=[],
        detail_level=body.detail_level.value if body.detail_level else "overview",
        purpose=norm_purpose,
        callback_base_url=settings.BACKEND_INTERNAL_URL,
        callback_token=callback_token,
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
                "SELECT id, title, is_public, created_at, updated_at FROM public.mind_maps WHERE id = $1",
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
    purpose: str | None = Form(DEFAULT_PURPOSE, description="Map purpose: general, study, or research"),
    file: UploadFile | str | None = File(None, description="Upload a single PDF or image"),
    files: list[UploadFile | str] | UploadFile | str | None = File(None, description="Or upload multiple files"),
    seed_url: str | None = Form(None, description="Optional public URL to use as generation seed (S3-BE7)"),
    uid: UUID = Depends(current_user_id),
):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")

    topic = (topic or "").strip() or None
    ideas = (ideas or "").strip() or None
    combined_files = []

    # S3-BE7: normalise and validate the optional URL seed
    seed_url = (seed_url or "").strip() or None
    if seed_url:
        url_ok, url_err = validate_seed_url(seed_url)
        if not url_ok:
            raise HTTPException(422, url_err)

    # S3-FE5 / S3-BE7: normalise and validate the optional purpose
    p_ok, norm_purpose, p_err = validate_purpose(purpose)
    if not p_ok:
        raise HTTPException(422, p_err)

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
    if not (topic or ideas or has_files or seed_url):
        raise HTTPException(422, "Provide a topic, some ideas, a file, or a seed URL.")
    if topic and len(topic) > 500:
        raise HTTPException(422, "Topic is too long (max 500 characters).")
    if ideas and len(ideas) > 20000:
        raise HTTPException(422, "Ideas text is too long (max 20000 characters).")

    if await pool.fetchval("SELECT 1 FROM public.nodes WHERE map_id = $1 LIMIT 1", map_id):
        raise HTTPException(409, "This map already has content. Use regenerate instead.")
    if await has_active_job(pool, map_id):
        raise HTTPException(409, "Generation already in progress for this map.")

    await limiter.check(uid, api="generation", redis=get_redis_pool(request))

    job_id = uuid4()
    callback_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(callback_token.encode("utf-8")).hexdigest()

    # Step 1: Create chat thread & user turn, then create the job record in DB upfront
    summary_msg = (
        f"Generate mind map for topic: '{topic or 'General'}' "
        f"(Detail: {detail_level.value if detail_level else 'overview'}, "
        f"Purpose: {norm_purpose})"
    )
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
        # Step 2: Store files using the exact same job_id in the storage path
        uploads = []
        if combined_files:
            items = await read_uploads(combined_files)  # type: ignore[arg-type]
            uploads = await store_files(uid, map_id, job_id, items)
            stored_keys = [u.storage_key for u in uploads if getattr(u, "storage_key", None)]

        # Step 3: Dispatch to ARQ worker (canonical queue execution path)
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
        gen_req = GenerationRequest(
            job_id=job_id,
            map_id=map_id,
            user_id=uid,
            job_type=JobType.GENERATE,
            topic=topic,
            ideas=ideas,
            files=file_refs,
            seed_has_files=bool(file_refs),
            detail_level=detail_level.value if detail_level else None,
            purpose=norm_purpose,
            callback_base_url=settings.BACKEND_INTERNAL_URL,
            callback_token=callback_token,
            seed_url=seed_url,
        )
        enqueued_id = await enqueue_generation_job(redis_pool, gen_req.model_dump(mode="json"))
        if not enqueued_id:
            raise RuntimeError("Redis declined job enqueue")
    except HTTPException as exc:
        logger.exception("Failed during file storage or queue dispatch for map %s, job %s: %s", map_id, job_id, exc)
        # Compensating cleanup: delete any uploaded files from Supabase Storage
        if stored_keys:
            try:
                await asyncio.to_thread(admin.storage.from_("seed-assets").remove, stored_keys)
            except Exception as rm_exc:
                logger.warning("Failed to remove storage keys %s during compensation: %s", stored_keys, rm_exc)
        # Transition job to lifecycle-valid 'rejected'
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
        # Compensating cleanup: delete any uploaded files from Supabase Storage
        if stored_keys:
            try:
                await asyncio.to_thread(admin.storage.from_("seed-assets").remove, stored_keys)
            except Exception as rm_exc:
                logger.warning("Failed to remove storage keys %s during compensation: %s", stored_keys, rm_exc)
        # Transition job to lifecycle-valid 'rejected'
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

    await limiter.check(uid, api="generation", redis=get_redis_pool(request))

    redis_pool = get_redis_pool(request)
    if not redis_pool:
        raise HTTPException(503, "Generation queue service is unavailable. Redis connection is required.")

    job_id = uuid4()
    callback_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(callback_token.encode("utf-8")).hexdigest()

    # Step 1: Create chat thread & user turn, then create the job record and safety snapshot in DB upfront
    summary_msg = f"Regenerate mind map with instruction: '{body.instruction.strip()}'"
    async with transaction(pool) as conn:
        expected_map_version = await lock_map_for_mutation(conn, map_id)
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
        await conn.execute(
            "UPDATE public.generation_jobs SET base_map_version = $2 WHERE id = $1",
            job_id,
            expected_map_version,
        )
        await create_map_snapshot(
            conn,
            map_id=map_id,
            trigger_job_id=job_id,
            reason=f"Pre-regeneration snapshot before full map regeneration ({job_id})",
        )

    try:
        has_file_val = await pool.fetchval(
            """
            SELECT 1 FROM public.seed_assets a
            JOIN public.generation_jobs j ON j.id = a.generation_job_id
            WHERE j.map_id = $1 AND a.asset_type IN ('pdf', 'image', 'docx', 'pptx')
            LIMIT 1
            """,
            map_id,
        )
        has_file_seed = bool(has_file_val)
        gen_req = GenerationRequest(
            job_id=job_id,
            map_id=map_id,
            user_id=uid,
            job_type=JobType.REGENERATE_FULL,
            expected_map_version=expected_map_version,
            regeneration_instruction=body.instruction.strip(),
            seed_has_files=bool(has_file_seed),
            detail_level=body.detail_level.value if body.detail_level else None,
            callback_base_url=settings.BACKEND_INTERNAL_URL,
            callback_token=callback_token,
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


# ------------------------------------------------------------------ S2-BE2: Node Regeneration
@router.post("/maps/{map_id}/nodes/{node_id}/regenerate", status_code=202)
async def regenerate_node(
    map_id: UUID,
    node_id: UUID,
    request: Request,
    body: NodeRegenerateRequest | None = None,
    uid: UUID = Depends(current_user_id),
):
    """Initiate partial, isolated regeneration of a mind map sub-tree rooted at node_id."""
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")

    target_node = await get_node_by_id(pool, map_id, node_id)
    if not target_node:
        raise HTTPException(404, "Node not found on this map")

    if await has_active_node_job(pool, map_id, node_id):
        raise HTTPException(409, "Regeneration already in progress for this node.")
    if await has_active_job(pool, map_id):
        raise HTTPException(409, "Generation already in progress for this map.")

    req_body = body or NodeRegenerateRequest()

    # Resolve instruction
    instruction: str | None = None
    if req_body.instruction and req_body.instruction.strip():
        instruction = req_body.instruction.strip()
    elif req_body.comment_id:
        comment = await get_node_comment(pool, map_id, req_body.comment_id)
        if not comment or comment["node_id"] != node_id:
            raise HTTPException(404, "Comment not found on this node")
        instruction = comment["content"]
    else:
        # Fall back to latest comment on node, or target node label
        comments = await list_node_comments(pool, map_id, node_id)
        if comments:
            instruction = comments[-1]["content"]
        else:
            instruction = f"Refine and expand the mind map branch for node '{target_node['label']}'"

    await limiter.check(uid, api="generation", redis=get_redis_pool(request))

    redis_pool = get_redis_pool(request)
    if not redis_pool:
        raise HTTPException(503, "Generation queue service is unavailable. Redis connection is required.")

    # Compute branch context
    edges_rows = await list_map_edges(pool, map_id)
    edges_data = [dict(e) for e in edges_rows]
    nodes_rows = await list_map_nodes(pool, map_id)
    nodes_map = {str(n["id"]): dict(n) for n in nodes_rows}

    descendant_ids = get_node_descendant_ids(edges_data, node_id)
    branch_node_ids = {str(node_id)} | {str(d) for d in descendant_ids}

    branch_nodes = [nodes_map[nid] for nid in branch_node_ids if nid in nodes_map]
    branch_edges = [
        e
        for e in edges_data
        if str(e.get("source_node_id") or e.get("source")) in branch_node_ids
        and str(e.get("target_node_id") or e.get("target")) in branch_node_ids
    ]

    # Compute ancestor labels across all incoming parent paths in multi-parent DAG
    ancestor_labels: list[str] = []
    ancestor_queue = deque([str(node_id)])
    visited_ancestors = {str(node_id)}
    while ancestor_queue:
        curr = ancestor_queue.popleft()
        for e in edges_data:
            tgt = str(e.get("target_node_id") or e.get("target"))
            src = str(e.get("source_node_id") or e.get("source"))
            if tgt == curr and src and src not in visited_ancestors:
                visited_ancestors.add(src)
                ancestor_queue.append(src)
                if src in nodes_map and nodes_map[src].get("label"):
                    ancestor_labels.append(nodes_map[src]["label"])
    ancestor_labels.reverse()

    incoming_parent_edges = [
        {
            "source_node_id": str(e.get("source_node_id") or e.get("source")),
            "target_node_id": str(e.get("target_node_id") or e.get("target")),
            "label": e.get("label"),
        }
        for e in edges_data
        if str(e.get("target_node_id") or e.get("target")) == str(node_id)
    ]
    branch_context = {
        "target_node_id": str(node_id),
        "target_node_label": target_node["label"],
        "target_node_description": target_node.get("description"),
        "descendant_ids": [str(d) for d in descendant_ids],
        "nodes": branch_nodes,
        "branch_nodes": branch_nodes,
        "edges": branch_edges,
        "branch_edges": branch_edges,
        "ancestor_labels": ancestor_labels,
        "parent_edges": incoming_parent_edges,
        "parent_edge": incoming_parent_edges[0] if incoming_parent_edges else None,
    }

    job_id = uuid4()
    callback_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(callback_token.encode("utf-8")).hexdigest()

    summary_msg = f"Regenerate branch for node '{target_node['label']}': '{instruction}'"
    expected_map_version: int
    raw_node_version = target_node.get("version")
    expected_node_version: int = int(raw_node_version) if raw_node_version is not None else 1
    async with transaction(pool) as conn:
        expected_map_version = await lock_map_for_mutation(conn, map_id)
        thread_id = await get_or_create_chat_thread(conn, map_id, uid)
        msg_id = await insert_chat_message(conn, thread_id, "user", summary_msg)
        await create_generation_job(
            conn,
            map_id=map_id,
            job_type=JobType.REGENERATE_NODE,
            job_id=job_id,
            target_node_id=node_id,
            triggered_by_message_id=msg_id,
            callback_token_hash=token_hash,
        )
        await conn.execute(
            "UPDATE public.generation_jobs SET base_map_version = $2 WHERE id = $1",
            job_id,
            expected_map_version,
        )
        # Take pre-regeneration safety snapshot inside the transaction
        snapshot_id = await create_map_snapshot(
            conn,
            map_id=map_id,
            target_node_id=node_id,
            trigger_job_id=job_id,
            reason=f"Pre-regeneration snapshot for node '{target_node['label']}' ({node_id})",
        )

    try:
        has_file_val = await pool.fetchval(
            """
            SELECT 1 FROM public.seed_assets a
            JOIN public.generation_jobs j ON j.id = a.generation_job_id
            WHERE j.map_id = $1 AND a.asset_type IN ('pdf', 'image', 'docx', 'pptx')
            LIMIT 1
            """,
            map_id,
        )
        has_file_seed = bool(has_file_val)
        gen_req = GenerationRequest(
            job_id=job_id,
            map_id=map_id,
            user_id=uid,
            job_type=JobType.REGENERATE_NODE,
            target_node_id=node_id,
            ancestor_labels=ancestor_labels,
            branch_context=branch_context,
            regeneration_instruction=instruction,
            seed_has_files=bool(has_file_seed),
            detail_level=req_body.detail_level,
            expected_map_version=expected_map_version,
            expected_node_version=expected_node_version,
            callback_base_url=settings.BACKEND_INTERNAL_URL,
            callback_token=callback_token,
        )
        enqueued_id = await enqueue_generation_job(redis_pool, gen_req.model_dump(mode="json"))
        if not enqueued_id:
            raise RuntimeError("Redis declined job enqueue")
    except HTTPException as exc:
        logger.exception("Failed during queue dispatch for map %s, node %s, job %s: %s", map_id, node_id, job_id, exc)
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
        logger.exception("Failed during queue dispatch for map %s, node %s, job %s: %s", map_id, node_id, job_id, exc)
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
        "target_node_id": node_id,
        "snapshot_id": snapshot_id,
        "poll": f"/api/maps/{map_id}/jobs/{job_id}",
    }


@router.post("/nodes/{node_id}/regenerate", status_code=202)
async def regenerate_node_direct(
    node_id: UUID,
    request: Request,
    body: NodeRegenerateRequest | None = None,
    uid: UUID = Depends(current_user_id),
):
    """Alias for POST /nodes/{node_id}/regenerate, resolving map_id automatically."""
    pool = pool_of(request)
    node_row = await pool.fetchrow("SELECT map_id FROM public.nodes WHERE id = $1", node_id)
    if not node_row:
        raise HTTPException(404, "Node not found")
    map_id = node_row["map_id"]
    return await regenerate_node(
        map_id=map_id,
        node_id=node_id,
        request=request,
        body=body,
        uid=uid,
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
    """Allow user to inspect their generation job status directly using job_id."""
    pool = pool_of(request)
    row = await pool.fetchrow(
        f"SELECT {JOB_COLS} FROM public.generation_jobs WHERE id = $1",
        job_id,
    )
    if not row:
        raise HTTPException(404, "Job not found")
    if not await assert_map_owner(pool, row["map_id"], uid):
        raise HTTPException(404, "Job not found")
    return job_dict(row)


@router.get("/maps/{map_id}/chat")
async def get_chat(map_id: UUID, request: Request, uid: UUID = Depends(current_user_id)):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")
    return await get_chat_messages(pool, map_id)


# ------------------------------------------------------------------ Node CRUD
@router.post("/maps/{map_id}/nodes", status_code=201)
@router.post("/api/maps/{map_id}/nodes", status_code=201, include_in_schema=False)
async def create_node_route(
    map_id: UUID,
    body: NodeCreate,
    request: Request,
    uid: UUID = Depends(current_user_id),
):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")

    node_id = uuid4()
    edge_id = uuid4() if body.parent_node_id else None

    async with transaction(pool) as conn:
        await lock_map_for_mutation(conn, map_id)
        existing_nodes = await list_map_nodes(conn, map_id)
        parent_id = body.parent_node_id

        # M6 & DR-02: Prevent creating a disconnected second root when nodes already exist
        if existing_nodes and not parent_id:
            from maps.graph_validator import find_root_node_id

            edges_rows = await list_map_edges(conn, map_id)
            edges_data = [dict(e) for e in edges_rows]
            nodes_data = [dict(n) for n in existing_nodes]
            root_id = find_root_node_id(nodes_data, edges_data)
            # U5/R5: Guard against IndexError if existing_nodes is empty
            parent_id = root_id if root_id else (UUID(str(existing_nodes[0]["id"])) if existing_nodes else None)

        if parent_id:
            parent_node = await get_node_by_id(conn, map_id, parent_id)
            if not parent_node:
                raise HTTPException(404, "Parent node not found on this map")

        created_node = await create_map_node(
            conn,
            node_id=node_id,
            map_id=map_id,
            label=body.label.strip(),
            description=body.description.strip() if body.description else None,
            pos_x=body.pos_x,
            pos_y=body.pos_y,
            background_color=body.background_color,
            text_color=body.text_color,
            width=body.width,
            height=body.height,
            node_type=body.node_type,
            node_data=body.node_data,
        )

        created_edge = None
        if parent_id:
            created_edge = await create_map_edge(
                conn,
                edge_id=edge_id or uuid4(),
                map_id=map_id,
                source_node_id=parent_id,
                target_node_id=node_id,
                label=body.edge_label.strip() if body.edge_label else None,
            )

        new_version = await check_and_bump_map_version(conn, map_id)

    res = dict(created_node)
    if created_edge:
        res["edge"] = dict(created_edge)
    res["map_version"] = new_version
    return res


@router.get("/maps/{map_id}/nodes", status_code=200)
async def list_nodes_route(
    map_id: UUID,
    request: Request,
    uid: UUID = Depends(current_user_id),
):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")
    nodes = await list_map_nodes(pool, map_id)
    return [dict(n) for n in nodes]


@router.get("/maps/{map_id}/nodes/{node_id}", status_code=200)
async def get_node_route(
    map_id: UUID,
    node_id: UUID,
    request: Request,
    uid: UUID = Depends(current_user_id),
):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")
    node = await get_node_by_id(pool, map_id, node_id)
    if not node:
        raise HTTPException(404, "Node not found on this map")
    return dict(node)


@router.patch("/maps/{map_id}/nodes/{node_id}", status_code=200)
async def update_node_route(
    map_id: UUID,
    node_id: UUID,
    body: NodeUpdate,
    request: Request,
    uid: UUID = Depends(current_user_id),
):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")

    existing = await get_node_by_id(pool, map_id, node_id)
    if not existing:
        raise HTTPException(404, "Node not found on this map")

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return dict(existing)

    async with transaction(pool) as conn:
        await lock_map_for_mutation(conn, map_id)
        updated = await update_map_node(conn, map_id, node_id, updates)
        if not updated:
            raise HTTPException(404, "Node not found on this map")
        new_version = await check_and_bump_map_version(conn, map_id)

    res = dict(updated)
    res["map_version"] = new_version
    return res


@router.delete("/maps/{map_id}/nodes/{node_id}", status_code=200)
async def delete_node_route(
    map_id: UUID,
    node_id: UUID,
    request: Request,
    cascade: bool = False,
    uid: UUID = Depends(current_user_id),
):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")

    existing = await get_node_by_id(pool, map_id, node_id)
    if not existing:
        raise HTTPException(404, "Node not found on this map")

    edges = await list_map_edges(pool, map_id)
    child_edges = [e for e in edges if e["source_node_id"] == node_id]

    if child_edges and not cascade:
        raise HTTPException(
            status_code=400,
            detail="Node has children. Set cascade=true to delete the node and its subtree.",
        )

    targets_to_delete: set[UUID] = {node_id}
    if cascade:
        descendants = get_node_descendant_ids(edges, node_id)
        targets_to_delete.update(descendants)

    async with transaction(pool) as conn:
        await lock_map_for_mutation(conn, map_id)
        deleted = await delete_map_node(conn, map_id, node_id, targets_to_delete)
        if not deleted:
            raise HTTPException(404, "Node not found on this map")
        new_version = await check_and_bump_map_version(conn, map_id)

    return {
        "message": "Node deleted successfully",
        "deleted_count": len(deleted),
        "deleted_node_ids": [str(nid) for nid in deleted],
        "map_version": new_version,
    }


# ------------------------------------------------------------------ Edge CRUD
@router.post("/maps/{map_id}/edges", status_code=201)
async def create_edge_route(
    map_id: UUID,
    body: EdgeCreate,
    request: Request,
    uid: UUID = Depends(current_user_id),
):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")

    if body.source_node_id == body.target_node_id:
        raise HTTPException(422, "Self-loops are not allowed. An edge cannot connect a node to itself.")

    src_node = await get_node_by_id(pool, map_id, body.source_node_id)
    if not src_node:
        raise HTTPException(404, "Source node does not belong to this map")

    tgt_node = await get_node_by_id(pool, map_id, body.target_node_id)
    if not tgt_node:
        raise HTTPException(404, "Target node does not belong to this map")

    existing_edges = await list_map_edges(pool, map_id)

    for e in existing_edges:
        if (
            e["source_node_id"] == body.source_node_id
            and e["target_node_id"] == body.target_node_id
            and (e["label"] or None) == (body.label or None)
        ):
            raise HTTPException(409, "Edge with identical endpoints and label already exists.")

    if check_edge_creates_cycle(existing_edges, body.source_node_id, body.target_node_id):
        raise HTTPException(
            status_code=422,
            detail="Adding this edge would create a cycle. Mind maps must remain strictly acyclic hierarchies.",
        )

    edge_id = uuid4()
    async with transaction(pool) as conn:
        await lock_map_for_mutation(conn, map_id)
        created_edge = await create_map_edge(
            conn,
            edge_id=edge_id,
            map_id=map_id,
            source_node_id=body.source_node_id,
            target_node_id=body.target_node_id,
            label=body.label.strip() if body.label else None,
            edge_data=body.edge_data,
        )
        new_version = await check_and_bump_map_version(conn, map_id)

    res = dict(created_edge)
    res["map_version"] = new_version
    return res


@router.get("/maps/{map_id}/edges", status_code=200)
async def list_edges_route(
    map_id: UUID,
    request: Request,
    uid: UUID = Depends(current_user_id),
):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")
    edges = await list_map_edges(pool, map_id)
    return [dict(e) for e in edges]


@router.get("/maps/{map_id}/edges/{edge_id}", status_code=200)
async def get_edge_route(
    map_id: UUID,
    edge_id: UUID,
    request: Request,
    uid: UUID = Depends(current_user_id),
):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")
    edge = await get_edge_by_id(pool, map_id, edge_id)
    if not edge:
        raise HTTPException(404, "Edge not found on this map")
    return dict(edge)


@router.patch("/maps/{map_id}/edges/{edge_id}", status_code=200)
async def update_edge_route(
    map_id: UUID,
    edge_id: UUID,
    body: EdgeUpdate,
    request: Request,
    uid: UUID = Depends(current_user_id),
):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")

    existing = await get_edge_by_id(pool, map_id, edge_id)
    if not existing:
        raise HTTPException(404, "Edge not found on this map")

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return dict(existing)

    is_endpoint_update = ("source_node_id" in updates and updates["source_node_id"] is not None) or (
        "target_node_id" in updates and updates["target_node_id"] is not None
    )

    if is_endpoint_update:
        new_src = (
            UUID(str(updates["source_node_id"]))
            if "source_node_id" in updates and updates["source_node_id"] is not None
            else UUID(str(existing.get("source_node_id")))
        )
        new_tgt = (
            UUID(str(updates["target_node_id"]))
            if "target_node_id" in updates and updates["target_node_id"] is not None
            else UUID(str(existing.get("target_node_id")))
        )

        if new_src == new_tgt:
            raise HTTPException(400, "Self-loops are not allowed (source and target nodes must differ)")

        if "source_node_id" in updates and updates["source_node_id"] is not None:
            src = await get_node_by_id(pool, map_id, new_src)
            if not src:
                raise HTTPException(422, "Source node does not belong to this map")
        if "target_node_id" in updates and updates["target_node_id"] is not None:
            tgt = await get_node_by_id(pool, map_id, new_tgt)
            if not tgt:
                raise HTTPException(422, "Target node does not belong to this map")

        all_edges_rows = await list_map_edges(pool, map_id)
        other_edges = [dict(e) for e in all_edges_rows if str(e["id"]) != str(edge_id)]
        if check_edge_creates_cycle(other_edges, new_src, new_tgt):
            raise HTTPException(400, "Updating this edge would create a cycle in the mind map hierarchy")

    async with transaction(pool) as conn:
        await lock_map_for_mutation(conn, map_id)
        updated = await update_map_edge(conn, map_id, edge_id, updates)
        if not updated:
            raise HTTPException(404, "Edge not found on this map")
        new_version = await check_and_bump_map_version(conn, map_id)

    res = dict(updated)
    res["map_version"] = new_version
    return res


@router.delete("/maps/{map_id}/edges/{edge_id}", status_code=200)
async def delete_edge_route(
    map_id: UUID,
    edge_id: UUID,
    request: Request,
    uid: UUID = Depends(current_user_id),
):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")

    existing = await get_edge_by_id(pool, map_id, edge_id)
    if not existing:
        raise HTTPException(404, "Edge not found on this map")

    async with transaction(pool) as conn:
        await lock_map_for_mutation(conn, map_id)
        await delete_map_edge(conn, map_id, edge_id)
        new_version = await check_and_bump_map_version(conn, map_id)

    return {"message": "Edge deleted successfully", "id": str(edge_id), "map_version": new_version}


# ------------------------------------------------------------------ Batch Graph Updates & Validation
@router.post("/maps/{map_id}/graph/batch", status_code=200)
@router.put("/maps/{map_id}/graph", status_code=200)
@router.patch("/maps/{map_id}/graph", status_code=200)
async def batch_update_graph(
    map_id: UUID,
    body: BatchGraphUpdateRequest,
    request: Request,
    uid: UUID = Depends(current_user_id),
):
    """Atomic multi-operation graph modification supporting optimistic versioning."""
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")

    # F2 & F9: Validate edge endpoints BEFORE version bump
    if body.nodes is not None and body.edges is not None:
        incoming_node_ids = {n.id for n in body.nodes if n.id}
        for e in body.edges:
            if e.source_node_id == e.target_node_id:
                raise HTTPException(422, "Self-loops are not allowed")
            if e.source_node_id not in incoming_node_ids or e.target_node_id not in incoming_node_ids:
                raise HTTPException(
                    422,
                    f"Edge endpoints ({e.source_node_id} -> {e.target_node_id}) must belong to this map.",
                )

    async with transaction(pool) as conn:
        try:
            new_version = await check_and_bump_map_version(conn, map_id, expected_version=body.expected_version)
        except OptimisticConcurrencyError as err:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "OptimisticLockConflict",
                    "message": (
                        f"Graph has been modified by another operation. "
                        f"Current version is {err.current_version}, but expected {err.expected_version}."
                    ),
                    "current_version": err.current_version,
                    "expected_version": err.expected_version,
                },
            )

        if body.nodes is not None and body.edges is not None:
            # Safe non-destructive delta reconciliation:
            # Preserves existing node IDs, node_comments foreign key cascades, and node versions.
            existing_node_rows = await conn.fetch("SELECT id FROM public.nodes WHERE map_id = $1", map_id)
            existing_node_ids = {r["id"] for r in existing_node_rows}
            incoming_node_map = {n.id: n for n in body.nodes if n.id}

            # 1. Prune nodes not in incoming payload
            nodes_to_delete = existing_node_ids - set(incoming_node_map.keys())
            if nodes_to_delete:
                await conn.execute(
                    "DELETE FROM public.nodes WHERE map_id = $1 AND id = ANY($2::uuid[])",
                    map_id,
                    list(nodes_to_delete),
                )

            # 2. Update existing nodes in place
            for nid, n in incoming_node_map.items():
                if nid in existing_node_ids:
                    updates: dict[str, Any] = {
                        "label": n.label.strip(),
                        "description": n.description.strip() if n.description else None,
                        "pos_x": n.pos_x,
                        "pos_y": n.pos_y,
                        "background_color": n.background_color,
                        "text_color": n.text_color,
                        "width": n.width,
                        "height": n.height,
                        "node_type": n.node_type,
                    }
                    if n.node_data is not None:
                        updates["node_data"] = n.node_data
                    await update_map_node(conn, map_id, nid, updates)

            # 3. Create newly added nodes
            for n in body.nodes:
                if not n.id or n.id not in existing_node_ids:
                    nid = n.id or uuid4()
                    await create_map_node(
                        conn,
                        node_id=nid,
                        map_id=map_id,
                        label=n.label.strip(),
                        description=n.description.strip() if n.description else None,
                        pos_x=n.pos_x,
                        pos_y=n.pos_y,
                        background_color=n.background_color,
                        text_color=n.text_color,
                        width=n.width,
                        height=n.height,
                        node_type=n.node_type,
                        node_data=n.node_data,
                    )

            # 4. Reconcile edges non-destructively
            existing_edge_rows = await conn.fetch(
                "SELECT id, source_node_id, target_node_id FROM public.edges WHERE map_id = $1",
                map_id,
            )
            existing_edge_ids = {r["id"] for r in existing_edge_rows if r.get("id") is not None}
            existing_pair_to_id = {
                (r["source_node_id"], r["target_node_id"]): r.get("id")
                for r in existing_edge_rows
                if "source_node_id" in r and "target_node_id" in r and r.get("id") is not None
            }
            incoming_edge_map = {}
            for e in body.edges:
                eid = e.id or existing_pair_to_id.get((e.source_node_id, e.target_node_id))
                if eid:
                    incoming_edge_map[eid] = e

            edges_to_delete = existing_edge_ids - set(incoming_edge_map.keys())
            if edges_to_delete:
                await conn.execute(
                    "DELETE FROM public.edges WHERE map_id = $1 AND id = ANY($2::uuid[])",
                    map_id,
                    list(edges_to_delete),
                )

            for eid, e in incoming_edge_map.items():
                if eid in existing_edge_ids:
                    edge_updates: dict[str, Any] = {}
                    if e.source_node_id is not None:
                        edge_updates["source_node_id"] = e.source_node_id
                    if e.target_node_id is not None:
                        edge_updates["target_node_id"] = e.target_node_id
                    if e.label is not None:
                        edge_updates["label"] = e.label.strip()
                    if e.edge_data is not None:
                        edge_updates["edge_data"] = e.edge_data
                    if edge_updates:
                        await update_map_edge(conn, map_id, eid, edge_updates)

            for e in body.edges:
                eid = e.id or existing_pair_to_id.get((e.source_node_id, e.target_node_id))
                if not eid or eid not in existing_edge_ids:
                    new_eid = eid or uuid4()
                    await create_map_edge(
                        conn,
                        edge_id=new_eid,
                        map_id=map_id,
                        source_node_id=e.source_node_id,
                        target_node_id=e.target_node_id,
                        label=e.label.strip() if e.label else None,
                        edge_data=e.edge_data,
                    )
        else:
            # Unconditional same-map referential check on delta edge endpoints
            endpoints_to_check = set()
            for ce in body.create_edges:
                endpoints_to_check.add(ce.source_node_id)
                endpoints_to_check.add(ce.target_node_id)
            for ue in body.update_edges:
                if ue.source_node_id:
                    endpoints_to_check.add(ue.source_node_id)
                if ue.target_node_id:
                    endpoints_to_check.add(ue.target_node_id)

            if endpoints_to_check:
                created_node_ids = {cn.id for cn in body.create_nodes if cn.id}
                need_verify = endpoints_to_check - created_node_ids
                if need_verify:
                    verified_rows = await conn.fetch(
                        "SELECT id FROM public.nodes WHERE map_id = $1 AND id = ANY($2::uuid[])",
                        map_id,
                        list(need_verify),
                    )
                    verified_ids = {r["id"] for r in verified_rows}
                    if not need_verify.issubset(verified_ids):
                        raise HTTPException(422, "Edge endpoints must belong to the same mind map")

            if body.delete_edge_ids:
                await conn.execute(
                    "DELETE FROM public.edges WHERE map_id = $1 AND id = ANY($2::uuid[])",
                    map_id,
                    body.delete_edge_ids,
                )

            if body.delete_node_ids:
                await conn.execute(
                    "DELETE FROM public.nodes WHERE map_id = $1 AND id = ANY($2::uuid[])",
                    map_id,
                    body.delete_node_ids,
                )

            for n in body.create_nodes:
                nid = n.id or uuid4()
                await create_map_node(
                    conn,
                    node_id=nid,
                    map_id=map_id,
                    label=n.label.strip(),
                    description=n.description.strip() if n.description else None,
                    pos_x=n.pos_x,
                    pos_y=n.pos_y,
                    background_color=n.background_color,
                    text_color=n.text_color,
                    width=n.width,
                    height=n.height,
                    node_type=n.node_type,
                    node_data=n.node_data,
                )

            for upd in body.update_nodes:
                u_dict = upd.model_dump(exclude_unset=True, exclude={"id"})
                if u_dict:
                    await update_map_node(conn, map_id, upd.id, u_dict)

            for e in body.create_edges:
                eid = e.id or uuid4()
                await create_map_edge(
                    conn,
                    edge_id=eid,
                    map_id=map_id,
                    source_node_id=e.source_node_id,
                    target_node_id=e.target_node_id,
                    label=e.label.strip() if e.label else None,
                    edge_data=e.edge_data,
                )

            for e_upd in body.update_edges:
                eu_dict = e_upd.model_dump(exclude_unset=True, exclude={"id"})
                if eu_dict:
                    await update_map_edge(conn, map_id, e_upd.id, eu_dict)

        nodes_rows = await list_map_nodes(conn, map_id)
        if not nodes_rows:
            raise HTTPException(
                status_code=422,
                detail="Batch graph update rejected: applying it would leave the mind map with zero nodes.",
            )
        edges_rows = await list_map_edges(conn, map_id)

        if body.validate_graph:
            val_errors = validate_graph_structure(nodes_rows, edges_rows, map_id=map_id)
            if val_errors:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "GraphValidationError",
                        "message": "Graph topology validation failed after applying batch updates.",
                        "errors": val_errors,
                    },
                )

    return {
        "status": "success",
        "map_id": map_id,
        "version": new_version,
        "nodes_count": len(nodes_rows),
        "edges_count": len(edges_rows),
        "nodes": [dict(n) for n in nodes_rows],
        "edges": [dict(e) for e in edges_rows],
    }


@router.post("/maps/{map_id}/graph/validate", status_code=200)
async def validate_graph_route(
    map_id: UUID,
    request: Request,
    body: ValidateGraphRequest | None = None,
    uid: UUID = Depends(current_user_id),
):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")

    if body and body.nodes is not None and body.edges is not None:
        nodes_data = [n.model_dump() for n in body.nodes]
        edges_data = [e.model_dump() for e in body.edges]
    else:
        nodes_rows = await list_map_nodes(pool, map_id)
        edges_rows = await list_map_edges(pool, map_id)
        nodes_data = [dict(n) for n in nodes_rows]
        edges_data = [dict(e) for e in edges_rows]

    errors = validate_graph_structure(nodes_data, edges_data, map_id=map_id)
    root_id = find_root_node_id(nodes_data, edges_data)

    return {
        "is_valid": len(errors) == 0,
        "root_id": root_id,
        "node_count": len(nodes_data),
        "edge_count": len(edges_data),
        "errors": errors,
    }


@router.get("/maps/{map_id}/graph")
async def get_graph(map_id: UUID, request: Request, uid: UUID = Depends(current_user_id)):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")
    version = await get_map_version(pool, map_id)
    nodes = await list_map_nodes(pool, map_id)
    edges = await list_map_edges(pool, map_id)
    return {
        "map_id": map_id,
        "version": version,
        "nodes": [dict(n) for n in nodes],
        "edges": [dict(e) for e in edges],
    }


# ------------------------------------------------------------------ S2-BE2: Node Comments CRUD
@router.post(
    "/maps/{map_id}/nodes/{node_id}/comments",
    status_code=201,
    response_model=CommentResponse,
)
async def create_comment_endpoint(
    map_id: UUID,
    node_id: UUID,
    body: CommentCreate,
    request: Request,
    uid: UUID = Depends(current_user_id),
):
    """Create a new comment or instruction on a node."""
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")

    node = await get_node_by_id(pool, map_id, node_id)
    if not node:
        raise HTTPException(404, "Node not found on this map")

    comment = await create_node_comment(
        pool,
        map_id=map_id,
        node_id=node_id,
        user_id=uid,
        content=body.content,
    )
    return CommentResponse(**comment)


@router.get(
    "/maps/{map_id}/nodes/{node_id}/comments",
    response_model=list[CommentResponse],
)
async def list_comments_endpoint(
    map_id: UUID,
    node_id: UUID,
    request: Request,
    uid: UUID = Depends(current_user_id),
):
    """List all comments on a specific node."""
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")

    node = await get_node_by_id(pool, map_id, node_id)
    if not node:
        raise HTTPException(404, "Node not found on this map")

    comments = await list_node_comments(pool, map_id, node_id)
    return [CommentResponse(**c) for c in comments]


@router.patch(
    "/maps/{map_id}/nodes/{node_id}/comments/{comment_id}",
    response_model=CommentResponse,
)
async def update_comment_endpoint(
    map_id: UUID,
    node_id: UUID,
    comment_id: UUID,
    body: CommentUpdate,
    request: Request,
    uid: UUID = Depends(current_user_id),
):
    """Update comment text."""
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")

    node = await get_node_by_id(pool, map_id, node_id)
    if not node:
        raise HTTPException(404, "Node not found on this map")

    existing = await get_node_comment(pool, map_id, comment_id)
    if not existing:
        raise HTTPException(404, "Comment not found")
    if existing["node_id"] != node_id:
        raise HTTPException(404, "Comment does not belong to this node")
    if body.content is not None and existing["user_id"] != uid:
        raise HTTPException(403, "You do not have permission to edit this comment")
    if existing["user_id"] != uid and not is_owner:
        raise HTTPException(403, "You do not have permission to edit this comment")

    updated = await update_node_comment(
        pool,
        map_id,
        comment_id,
        content=body.content,
        status=body.status,
    )
    if not updated:
        raise HTTPException(404, "Comment not found")
    return CommentResponse(**updated)


@router.delete(
    "/maps/{map_id}/nodes/{node_id}/comments/{comment_id}",
    status_code=204,
)
async def delete_comment_endpoint(
    map_id: UUID,
    node_id: UUID,
    comment_id: UUID,
    request: Request,
    uid: UUID = Depends(current_user_id),
):
    """Delete a comment from a node."""
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")

    node = await get_node_by_id(pool, map_id, node_id)
    if not node:
        raise HTTPException(404, "Node not found on this map")

    existing = await get_node_comment(pool, map_id, comment_id)
    if not existing:
        raise HTTPException(404, "Comment not found")
    if existing["node_id"] != node_id:
        raise HTTPException(404, "Comment does not belong to this node")
    if existing["user_id"] != uid and not is_owner:
        raise HTTPException(403, "You do not have permission to delete this comment")

    deleted = await delete_node_comment(pool, map_id, comment_id)
    if not deleted:
        raise HTTPException(404, "Comment not found")
    return None


# ------------------------------------------------------------------ S2-BE2: Branch Resolution
@router.get(
    "/maps/{map_id}/nodes/{node_id}/branch",
    response_model=BranchResponse,
)
async def get_node_branch_endpoint(
    map_id: UUID,
    node_id: UUID,
    request: Request,
    uid: UUID = Depends(current_user_id),
):
    """Retrieve isolated branch subgraph rooted at node_id, including descendants and parent connection."""
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")

    target_node = await get_node_by_id(pool, map_id, node_id)
    if not target_node:
        raise HTTPException(404, "Node not found on this map")

    all_nodes = await list_map_nodes(pool, map_id)
    all_edges = await list_map_edges(pool, map_id)

    edges_data = [dict(e) for e in all_edges]
    descendant_ids = get_node_descendant_ids(edges_data, node_id)
    branch_node_ids = {node_id} | descendant_ids

    branch_nodes = [dict(n) for n in all_nodes if n["id"] in branch_node_ids]
    branch_edges = [
        e
        for e in edges_data
        if (e.get("source_node_id") or e.get("source")) in branch_node_ids
        and (e.get("target_node_id") or e.get("target")) in branch_node_ids
    ]
    parent_edge = next(
        (e for e in edges_data if (e.get("target_node_id") or e.get("target")) == node_id),
        None,
    )

    return {
        "map_id": map_id,
        "target_node_id": node_id,
        "descendant_ids": list(descendant_ids),
        "node_count": len(branch_nodes),
        "edge_count": len(branch_edges),
        "nodes": branch_nodes,
        "edges": branch_edges,
        "parent_edge": parent_edge,
    }


# ------------------------------------------------------------------ S2-BE2: Snapshots API
@router.post(
    "/maps/{map_id}/snapshots",
    status_code=201,
)
@router.post(
    "/api/maps/{map_id}/snapshots",
    status_code=201,
    include_in_schema=False,
)
async def create_snapshot_endpoint(
    map_id: UUID,
    request: Request,
    body: SnapshotCreate | None = None,
    uid: UUID = Depends(current_user_id),
):
    """Take an immutable snapshot of current mind map graph state."""
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")

    reason = body.reason if body else "Manual snapshot"
    snapshot_id = await create_map_snapshot(pool, map_id=map_id, reason=reason)
    return {"id": snapshot_id, "message": "Snapshot created successfully"}


@router.get(
    "/maps/{map_id}/snapshots",
    response_model=list[SnapshotResponse],
)
@router.get(
    "/api/maps/{map_id}/snapshots",
    response_model=list[SnapshotResponse],
    include_in_schema=False,
)
async def list_snapshots_endpoint(
    map_id: UUID,
    request: Request,
    uid: UUID = Depends(current_user_id),
):
    """List historical snapshots for a mind map."""
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")

    snapshots = await list_map_snapshots(pool, map_id)
    result = []
    for s in snapshots:
        d = dict(s)
        raw_nodes = d.get("nodes_snapshot")
        raw_edges = d.get("edges_snapshot")
        if raw_nodes is None and isinstance(d.get("snapshot_data"), dict):
            raw_nodes = d["snapshot_data"].get("nodes")
            raw_edges = d["snapshot_data"].get("edges")
        if raw_nodes is None and isinstance(d.get("graph"), dict):
            raw_nodes = d["graph"].get("nodes")
            raw_edges = d["graph"].get("edges")

        if isinstance(raw_nodes, str):
            try:
                raw_nodes = json.loads(raw_nodes)
            except Exception:
                raw_nodes = []
        if isinstance(raw_edges, str):
            try:
                raw_edges = json.loads(raw_edges)
            except Exception:
                raw_edges = []

        raw_nodes = raw_nodes or []
        raw_edges = raw_edges or []

        d["snapshot_data"] = {
            "nodes": raw_nodes,
            "edges": raw_edges,
        }
        nc = d.get("nodes_count")
        d["nodes_count"] = nc if (nc is not None and isinstance(nc, int) and nc > 0) else len(raw_nodes)
        ec = d.get("edges_count")
        d["edges_count"] = ec if (ec is not None and isinstance(ec, int) and ec > 0) else len(raw_edges)
        d["version_number"] = d.get("version_number") or d.get("version_no") or 1
        d["trigger_source"] = d.get("trigger_source") or d.get("reason") or "manual"
        d["reason"] = d.get("reason") or d.get("trigger_source") or "Manual snapshot"
        result.append(SnapshotResponse(**d))
    return result


@router.get(
    "/maps/{map_id}/snapshots/{snapshot_id}",
    response_model=SnapshotDetailResponse,
)
@router.get(
    "/api/maps/{map_id}/snapshots/{snapshot_id}",
    response_model=SnapshotDetailResponse,
    include_in_schema=False,
)
async def get_snapshot_endpoint(
    map_id: UUID,
    snapshot_id: UUID,
    request: Request,
    uid: UUID = Depends(current_user_id),
):
    """Retrieve full details and node/edge payload for a specific snapshot."""
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")

    snapshot = await get_map_snapshot(pool, map_id, snapshot_id)
    if not snapshot:
        raise HTTPException(404, "Snapshot not found")

    raw_nodes = snapshot.get("nodes_snapshot")
    raw_edges = snapshot.get("edges_snapshot")
    if not raw_nodes and isinstance(snapshot.get("snapshot_data"), dict):
        raw_nodes = snapshot["snapshot_data"].get("nodes")
        raw_edges = snapshot["snapshot_data"].get("edges")
    if not raw_nodes and isinstance(snapshot.get("graph"), dict):
        raw_nodes = snapshot["graph"].get("nodes")
        raw_edges = snapshot["graph"].get("edges")

    if isinstance(raw_nodes, str):
        try:
            raw_nodes = json.loads(raw_nodes)
        except Exception:
            raw_nodes = []
    if isinstance(raw_edges, str):
        try:
            raw_edges = json.loads(raw_edges)
        except Exception:
            raw_edges = []

    raw_nodes = raw_nodes or []
    raw_edges = raw_edges or []

    return {
        "id": snapshot["id"],
        "map_id": snapshot["map_id"],
        "trigger_job_id": snapshot.get("trigger_job_id"),
        "target_node_id": snapshot.get("target_node_id"),
        "reason": snapshot.get("reason") or "Manual snapshot",
        "trigger_source": snapshot.get("trigger_source") or snapshot.get("reason") or "manual",
        "nodes_count": len(raw_nodes),
        "edges_count": len(raw_edges),
        "created_at": snapshot["created_at"],
        "nodes_snapshot": raw_nodes,
        "edges_snapshot": raw_edges,
        "snapshot_data": {"nodes": raw_nodes, "edges": raw_edges},
        "version_number": snapshot.get("version_number") or snapshot.get("version_no") or 1,
        "label": snapshot.get("label"),
    }


@router.post(
    "/snapshots/{snapshot_id}/restore",
    status_code=200,
)
@router.post(
    "/api/snapshots/{snapshot_id}/restore",
    status_code=200,
    include_in_schema=False,
)
async def restore_snapshot_direct(
    snapshot_id: UUID,
    request: Request,
    uid: UUID = Depends(current_user_id),
):
    """Direct alias for restoring a snapshot, looking up map_id automatically."""
    from snapshots.service import restore_snapshot_by_version_or_id

    pool = pool_of(request)
    map_row = await pool.fetchrow("SELECT map_id FROM public.map_snapshots WHERE id = $1", snapshot_id)
    if not map_row:
        raise HTTPException(404, "Snapshot not found")
    map_id = map_row["map_id"]
    result = await restore_snapshot_by_version_or_id(
        pool=pool,
        map_id=map_id,
        identifier=str(snapshot_id),
        user_id=uid,
    )
    if not result:
        raise HTTPException(404, f"Snapshot '{snapshot_id}' not found for map '{map_id}' or unauthorized.")
    return result


@router.delete(
    "/maps/{map_id}/snapshots/{snapshot_id}",
    status_code=200,
)
@router.delete(
    "/api/maps/{map_id}/snapshots/{snapshot_id}",
    status_code=200,
    include_in_schema=False,
)
async def delete_snapshot_endpoint(
    map_id: UUID,
    snapshot_id: UUID,
    request: Request,
    uid: UUID = Depends(current_user_id),
):
    """Delete a specific immutable snapshot of a mind map."""
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

    # Clean up associated files in Supabase Storage before removing database record
    await delete_map_storage_files(user_id=uid, map_id=map_id, pool=pool)

    await pool.execute("DELETE FROM public.mind_maps WHERE id = $1", map_id)
    return {"message": "Map deleted successfully"}
