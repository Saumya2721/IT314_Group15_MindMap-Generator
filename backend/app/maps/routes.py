"""Map and generation API routes.

Migrated cleanly from mm_backend.maps into backend.maps.routes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
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
    HTTPException,
    Query,
    Request,
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
from services.queue import enqueue_generation_job, get_redis_pool
from services.runner import limiter
from uploads.service import delete_map_storage_files, read_uploads, store_files

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
    restore_map_snapshot,
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
    is_public: bool | None = None
    view_x: float | None = Field(None, description="Canvas horizontal viewport coordinate")
    view_y: float | None = Field(None, description="Canvas vertical viewport coordinate")
    view_zoom: float | None = Field(None, gt=0, description="Canvas zoom level for the persisted viewport")


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
    uid: UUID = Depends(current_user_id),
):
    pool = pool_of(request)
    if q and q.strip():
        rows = await search_maps(pool, uid, q, limit)
    else:
        rows = await pool.fetch(
            """SELECT m.id, m.title, m.is_public, m.created_at, m.updated_at,
                      (SELECT count(*)::int FROM public.nodes n WHERE n.map_id = m.id) AS node_count,
                      (SELECT j.status FROM public.generation_jobs j WHERE j.map_id = m.id ORDER BY j.created_at DESC LIMIT 1) AS latest_job_status,
                      (SELECT j.progress_percent::int FROM public.generation_jobs j WHERE j.map_id = m.id ORDER BY j.created_at DESC LIMIT 1) AS latest_job_progress,
                      (SELECT j.id FROM public.generation_jobs j WHERE j.map_id = m.id ORDER BY j.created_at DESC LIMIT 1) AS latest_job_id
               FROM public.mind_maps m
               WHERE m.user_id = $1
               ORDER BY m.updated_at DESC""",
            uid,
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
    row = await pool.fetchrow(sql, *args)
    return dict(row)


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

    # Step 1: Create chat thread & user turn, then create the job record in DB upfront
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
            job_type=JobType.GENERATE,
            topic=topic,
            ideas=ideas,
            files=file_refs,
            detail_level=detail_level.value if detail_level else None,
            callback_base_url=settings.BACKEND_INTERNAL_URL,
            callback_token=callback_token,
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

    limiter.check(uid)

    job_id = uuid4()
    callback_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(callback_token.encode("utf-8")).hexdigest()

    # Step 1: Create chat thread & user turn, then create the job record in DB upfront
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
        await create_map_snapshot(
            conn,
            map_id=map_id,
            trigger_job_id=job_id,
            reason=f"Pre-regeneration snapshot before full map regeneration ({job_id})",
        )

    try:
        redis_pool = get_redis_pool(request)
        if not redis_pool:
            raise HTTPException(503, "Generation queue service is unavailable. Redis connection is required.")

        gen_req = GenerationRequest(
            job_id=job_id,
            map_id=map_id,
            job_type=JobType.REGENERATE_FULL,
            regeneration_instruction=body.instruction.strip(),
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

    # Take pre-regeneration safety snapshot
    snapshot_id = await create_map_snapshot(
        pool,
        map_id=map_id,
        target_node_id=node_id,
        reason=f"Pre-regeneration snapshot for node '{target_node['label']}' ({node_id})",
    )

    # Compute branch context
    edges_rows = await list_map_edges(pool, map_id)
    edges_data = [dict(e) for e in edges_rows]
    descendant_ids = get_node_descendant_ids(edges_data, node_id)
    parent_edge = next((e for e in edges_data if (e.get("target_node_id") or e.get("target")) == node_id), None)
    branch_context = {
        "target_node_id": str(node_id),
        "target_node_label": target_node["label"],
        "target_node_description": target_node.get("description"),
        "descendant_ids": [str(d) for d in descendant_ids],
        "parent_edge": {
            "source_node_id": str(parent_edge["source_node_id"]),
            "label": parent_edge.get("label"),
        }
        if parent_edge
        else None,
    }

    limiter.check(uid)

    job_id = uuid4()
    callback_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(callback_token.encode("utf-8")).hexdigest()

    summary_msg = f"Regenerate branch for node '{target_node['label']}': '{instruction}'"
    async with transaction(pool) as conn:
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

    try:
        redis_pool = get_redis_pool(request)
        if not redis_pool:
            raise HTTPException(503, "Generation queue service is unavailable. Redis connection is required.")

        gen_req = GenerationRequest(
            job_id=job_id,
            map_id=map_id,
            job_type=JobType.REGENERATE_NODE,
            target_node_id=node_id,
            branch_context=branch_context,
            regeneration_instruction=instruction,
            detail_level=req_body.detail_level,
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
    await assert_map_owner(pool, row["map_id"], uid)
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
        if body.parent_node_id:
            parent_node = await get_node_by_id(conn, map_id, body.parent_node_id)
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
        if body.parent_node_id and edge_id:
            created_edge = await create_map_edge(
                conn,
                edge_id=edge_id,
                map_id=map_id,
                source_node_id=body.parent_node_id,
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
        updated = await update_map_node(conn, map_id, node_id, updates)
        new_version = await check_and_bump_map_version(conn, map_id)

    res = dict(updated) if updated else dict(existing)
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
        deleted = await delete_map_node(conn, map_id, node_id, targets_to_delete)
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

    async with transaction(pool) as conn:
        updated = await update_map_edge(conn, map_id, edge_id, updates)
        new_version = await check_and_bump_map_version(conn, map_id)

    res = dict(updated) if updated else dict(existing)
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
            await conn.execute("DELETE FROM public.edges WHERE map_id = $1", map_id)
            await conn.execute("DELETE FROM public.nodes WHERE map_id = $1", map_id)

            for n in body.nodes:
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

            for e in body.edges:
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
        else:
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
    if existing["user_id"] != uid:
        raise HTTPException(403, "You do not have permission to edit this comment")

    updated = await update_node_comment(pool, map_id, comment_id, body.content)
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
        if "snapshot_data" in d and ("nodes_count" not in d or d["nodes_count"] == 0):
            snap_data = d.get("snapshot_data") or {}
            if isinstance(snap_data, dict):
                d.setdefault("nodes_count", len(snap_data.get("nodes", [])))
                d.setdefault("edges_count", len(snap_data.get("edges", [])))
        result.append(SnapshotResponse(**d))
    return result


@router.get(
    "/maps/{map_id}/snapshots/{snapshot_id}",
    response_model=SnapshotDetailResponse,
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

    if isinstance(raw_nodes, str):
        raw_nodes = json.loads(raw_nodes)
    if isinstance(raw_edges, str):
        raw_edges = json.loads(raw_edges)

    return {
        "id": snapshot["id"],
        "map_id": snapshot["map_id"],
        "trigger_job_id": snapshot.get("trigger_job_id"),
        "target_node_id": snapshot.get("target_node_id"),
        "reason": snapshot.get("reason") or "Manual snapshot",
        "nodes_count": len(raw_nodes or []),
        "edges_count": len(raw_edges or []),
        "created_at": snapshot["created_at"],
        "nodes_snapshot": raw_nodes or [],
        "edges_snapshot": raw_edges or [],
        "snapshot_data": snapshot.get("snapshot_data") or {"nodes": raw_nodes or [], "edges": raw_edges or []},
        "version_number": snapshot.get("version_number"),
        "label": snapshot.get("label"),
    }


@router.post(
    "/maps/{map_id}/snapshots/{snapshot_id}/restore",
    status_code=200,
)
async def restore_snapshot_endpoint(
    map_id: UUID,
    snapshot_id: UUID,
    request: Request,
    uid: UUID = Depends(current_user_id),
):
    """Restore map state from an immutable snapshot, taking a safety snapshot first."""
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")

    async with transaction(pool) as conn:
        try:
            result = await restore_map_snapshot(conn, map_id=map_id, snapshot_id=snapshot_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc))

    if isinstance(result, dict) and "snapshot_data" not in result:
        snapshot = await get_map_snapshot(pool, map_id, snapshot_id)
        if snapshot:
            r_nodes = snapshot.get("nodes_snapshot") or []
            r_edges = snapshot.get("edges_snapshot") or []
            result["snapshot_data"] = {"nodes": r_nodes, "edges": r_edges}

    return result


@router.post(
    "/snapshots/{snapshot_id}/restore",
    status_code=200,
)
async def restore_snapshot_direct(
    snapshot_id: UUID,
    request: Request,
    uid: UUID = Depends(current_user_id),
):
    """Direct alias for restoring a snapshot, looking up map_id automatically."""
    pool = pool_of(request)
    map_row = await pool.fetchrow("SELECT map_id FROM public.map_snapshots WHERE id = $1", snapshot_id)
    if not map_row:
        raise HTTPException(404, "Snapshot not found")
    map_id = map_row["map_id"]
    return await restore_snapshot_endpoint(
        map_id=map_id,
        snapshot_id=snapshot_id,
        request=request,
        uid=uid,
    )


@router.delete(
    "/maps/{map_id}/snapshots/{snapshot_id}",
    status_code=200,
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
