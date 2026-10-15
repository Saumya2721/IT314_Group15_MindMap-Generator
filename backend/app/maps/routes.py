"""Map and generation API routes.

Migrated cleanly from mm_backend.maps into backend.maps.routes.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from enum import Enum
from typing import Any, List, Optional, Union
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from pydantic import BaseModel, Field

from contracts.models import FileRef, GenerationRequest, JobType
from core.config import settings
from core.supabase import admin, current_user_id
from db.connection import transaction
from db.repository import (
    ActiveJobAlreadyExists,
    assert_map_owner,
    create_generation_job,
    get_chat_messages,
    get_job as repo_get_job,
    get_latest_job as repo_get_latest_job,
    get_map_graph,
    get_or_create_chat_thread,
    has_active_job,
    insert_chat_message,
)
from services.queue import enqueue_generation_job, get_redis_pool
from services.runner import limiter, run_generate, run_regenerate
from uploads.service import read_uploads, store_files

try:
    from seed.normalizer import SeedSubmission
except ImportError:
    from dataclasses import dataclass

    @dataclass
    class SeedSubmission:
        topic: str | None
        ideas: str | None
        files: list

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Maps & generation"])

JOB_COLS = "id, map_id, job_type, status, progress_percent, error_message, created_at, started_at, completed_at"


class DetailLevel(str, Enum):
    overview = "overview"
    balanced = "balanced"
    detailed = "detailed"


class CreateMap(BaseModel):
    title: str = Field("Untitled mind map", min_length=1, max_length=255)


class RegenerateRequest(BaseModel):
    instruction: str = Field(..., min_length=3, max_length=2000)
    detail_level: Optional[DetailLevel] = None


class QuickGenerateRequest(BaseModel):
    topic: Optional[str] = None
    ideas: Optional[str] = None
    detail_level: Optional[DetailLevel] = DetailLevel.overview
    map_id: Optional[UUID] = None


def pool_of(request: Request):
    return request.app.state.pool


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
async def list_maps(request: Request, uid: UUID = Depends(current_user_id)):
    rows = await pool_of(request).fetch(
        "SELECT id, title, is_public, created_at, updated_at FROM public.mind_maps "
        "WHERE user_id = $1 ORDER BY updated_at DESC",
        uid,
    )
    return [dict(r) for r in rows]


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
    topic: Optional[str] = Form(None),
    ideas: Optional[str] = Form(None),
    detail_level: Optional[DetailLevel] = Form(None),
    file: Optional[Union[UploadFile, str]] = File(None, description="Upload a single PDF or image"),
    files: Optional[Union[List[Union[UploadFile, str]], UploadFile, str]] = File(None, description="Or upload multiple files"),
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
        return obj is not None and hasattr(obj, "filename") and hasattr(obj, "read") and bool(getattr(obj, "filename", None))

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

    stored_keys: List[str] = []
    try:
        # Step 2: Store files using the exact same job_id in the storage path
        uploads = []
        if combined_files:
            items = await read_uploads(combined_files)
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


# ------------------------------------------------------------------ Reject Node Regeneration (501)
@router.post("/maps/{map_id}/nodes/{node_id}/regenerate", status_code=501)
async def regenerate_node(
    map_id: UUID,
    node_id: UUID,
    request: Request,
    uid: UUID = Depends(current_user_id),
):
    """Reject node-level regeneration at intake with 501 Not Implemented after verifying map ownership and node existence."""
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


@router.delete("/maps/{map_id}", status_code=200)
async def delete_map(map_id: UUID, request: Request, uid: UUID = Depends(current_user_id)):
    pool = pool_of(request)
    is_owner = await assert_map_owner(pool, map_id, uid)
    if not is_owner:
        raise HTTPException(404, "Map not found")
    await pool.execute("DELETE FROM public.mind_maps WHERE id = $1", map_id)
    return {"message": "Map deleted successfully"}
