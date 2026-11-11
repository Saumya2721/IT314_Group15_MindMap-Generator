"""Internal worker callback endpoints.

Receives authenticated progress updates and final results from the decoupled
LLM worker (as described in llm/api.md Section 3).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from typing import Any
from uuid import UUID

from contracts.models import GenerationResult
from core.supabase import bearer
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, model_validator
from services.generator import compute_layout

from db.connection import transaction
from db.models import EdgeRecord, NodeRecord, SeedAssetType
from db.repository import (
    create_map_snapshot,
    get_chat_messages,
    get_or_create_chat_thread,
    insert_chat_message,
    insert_seed_asset,
    next_snapshot_version,
    persist_generated_graph,
    replace_map_graph,
    update_chat_thread_summary,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/jobs", tags=["Internal Worker Callbacks"])


async def verify_callback_auth(
    job_id: UUID,
    request: Request,
    authorization: str | None = None,
    creds: HTTPAuthorizationCredentials | None = None,
) -> dict:
    """Validate worker's callback token or map owner's access token."""
    raw_token: str | None = None

    if creds and creds.credentials and creds.credentials.strip():
        raw_token = creds.credentials.strip()
    elif authorization and authorization.strip():
        clean_auth = authorization.strip()
        raw_token = clean_auth[7:].strip() if clean_auth.lower().startswith("bearer ") else clean_auth
    else:
        # Fallback to direct request headers inspection
        auth_hdr = request.headers.get("authorization") or request.headers.get("Authorization")
        if auth_hdr and auth_hdr.strip():
            clean_hdr = auth_hdr.strip()
            raw_token = clean_hdr[7:].strip() if clean_hdr.lower().startswith("bearer ") else clean_hdr
        elif request.cookies.get("access_token"):
            raw_token = request.cookies.get("access_token")

    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header. Bearer token required.",
        )

    # Strip any enclosing quotes if user pasted JSON string
    raw_token = raw_token.strip("\"'")

    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    pool = request.app.state.pool
    if not pool:
        raise HTTPException(status_code=503, detail="Database pool not initialized")

    row = await pool.fetchrow(
        "SELECT id, map_id, job_type, status, callback_token_hash FROM public.generation_jobs WHERE id = $1",
        job_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    expected_hash = row["callback_token_hash"]
    # Check if token matches the worker callback token hash
    if expected_hash and secrets.compare_digest(token_hash, expected_hash):
        return dict(row)

    # Allow authenticated user who owns the map to check job status / callbacks
    try:
        from core.supabase import anon_client

        client = anon_client()
        res = client.auth.get_user(raw_token)
        if res and res.user and res.user.id:
            owner_uid = UUID(str(res.user.id))
            is_owner = await pool.fetchval(
                "SELECT 1 FROM public.mind_maps WHERE id = $1 AND user_id = $2",
                row["map_id"],
                owner_uid,
            )
            if is_owner:
                return dict(row)
            else:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You do not have permission to access this mind map's job.",
                )
    except HTTPException as http_exc:
        if http_exc.status_code == status.HTTP_403_FORBIDDEN:
            raise
    except Exception as exc:
        logger.warning("Token verification failed for job %s: %s", job_id, exc)

    logger.warning("Callback authentication failed for job %s: invalid token", job_id)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid callback token for this job.",
    )


class ProgressPayload(BaseModel):
    progress_percent: int = Field(default=0, ge=0, le=100)

    @model_validator(mode="before")
    @classmethod
    def unify_fields(cls, values: Any) -> Any:
        if isinstance(values, dict):
            val = values.get("progress_percent") if "progress_percent" in values else values.get("percent")
            if val is not None:
                return {"progress_percent": int(val)}
        return values


@router.get("/{job_id}", status_code=200)
async def check_job_status(
    job_id: UUID,
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    authorization: str | None = Header(None),
):
    """Worker pre-flight or user check to query whether job has already reached a terminal state."""
    job_ctx = await verify_callback_auth(job_id, request, authorization, creds)
    st = job_ctx.get("status")
    return {
        "id": str(job_id),
        "status": st,
        "already_terminal": st in ("completed", "failed", "rejected"),
        "receipt_id": str(job_id) if st in ("completed", "failed", "rejected") else None,
    }


@router.post("/{job_id}/progress", status_code=204)
async def report_progress(
    job_id: UUID,
    payload: ProgressPayload,
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    authorization: str | None = Header(None),
):
    """Worker reports non-terminal progress milestone (authenticated, monotonic).

    Under chk_generation_job_lifecycle:
    - Running jobs must have started_at IS NOT NULL and progress_percent < 100.
    - Transitions pending jobs to running. 100% is exclusively set at terminal completion.
    """
    await verify_callback_auth(job_id, request, authorization, creds)
    pool = request.app.state.pool

    # Clamp progress_percent to max 99% during non-terminal ticks
    clamped_progress = min(max(payload.progress_percent, 0), 99)

    await pool.execute(
        """
        UPDATE public.generation_jobs
           SET status = 'running',
               progress_percent = GREATEST(progress_percent, $2),
               started_at = COALESCE(started_at, now())
         WHERE id = $1 AND status IN ('pending', 'running')
        """,
        job_id,
        clamped_progress,
    )
    logger.info("Job %s progress: %d%%", job_id, clamped_progress)
    return None


@router.post("/{job_id}/complete", status_code=200)
async def report_complete(
    job_id: UUID,
    result: GenerationResult,
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    authorization: str | None = Header(None),
):
    """Worker reports successful generation outcome with final nodes, edges, and seeds."""
    job_ctx = await verify_callback_auth(job_id, request, authorization, creds)
    pool = request.app.state.pool

    # Strict contract validation: assert identity and completion status
    if result.job_id != job_id:
        raise HTTPException(
            status_code=400,
            detail=f"job_id mismatch: URL is {job_id}, payload has {result.job_id}",
        )
    if str(job_ctx["map_id"]) != str(result.map_id):
        raise HTTPException(
            status_code=400,
            detail=f"map_id mismatch: job belongs to map {job_ctx['map_id']}, payload has {result.map_id}",
        )
    if result.status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{result.status}' for complete callback; expected 'completed'",
        )

    map_id = job_ctx["map_id"]
    job_type = job_ctx["job_type"]

    nodes_raw = [n.model_dump() for n in result.nodes]
    edges_raw = [e.model_dump() for e in result.edges]

    # Idempotent lock and check
    async with transaction(pool) as conn:
        job_row = await conn.fetchrow(
            "SELECT id, map_id, job_type, status, started_at FROM public.generation_jobs WHERE id = $1 FOR UPDATE",
            job_id,
        )
        if not job_row:
            raise HTTPException(404, "Job not found")

        # Idempotency: if already completed, return 200 with durable receipt without duplicating records
        if job_row["status"] == "completed":
            logger.info("Job %s already marked completed; returning idempotent success.", job_id)
            return {
                "status": "completed",
                "message": "Already processed",
                "receipt_id": str(job_id),
                "already_processed": True,
            }

        # Terminal state conflict: if job already failed or rejected, reject completion
        if job_row["status"] in ("failed", "rejected"):
            logger.warning(
                "Job %s already in terminal status %s; rejecting late completion callback.",
                job_id,
                job_row["status"],
            )
            raise HTTPException(
                status_code=409,
                detail=f"Cannot complete job: job has already reached terminal status '{job_row['status']}'",
            )

        # P1-7: Compute visual layout coordinates if nodes default to (0, 0)
        compute_layout(nodes_raw, edges_raw)

        node_records = [
            NodeRecord(
                id=UUID(str(n["id"])),
                map_id=map_id,
                label=n["label"],
                description=n.get("description"),
                pos_x=float(n.get("pos_x", 0.0)),
                pos_y=float(n.get("pos_y", 0.0)),
                background_color=n.get("background_color"),
                text_color=n.get("text_color"),
                node_type=n.get("node_type", "default"),
                source_job_id=job_id,
            )
            for n in nodes_raw
        ]

        edge_records = [
            EdgeRecord(
                id=UUID(str(e["id"])),
                map_id=map_id,
                source_node_id=UUID(str(e.get("source_node_id") or e.get("source"))),
                target_node_id=UUID(str(e.get("target_node_id") or e.get("target"))),
                label=e.get("label"),
            )
            for e in edges_raw
        ]

        # P1-8: Persist returned seed assets atomically
        for asset in result.seed_assets:
            asset_type_str = str(
                asset.asset_type.value if hasattr(asset.asset_type, "value") else asset.asset_type
            ).lower()
            asset_type = (
                SeedAssetType.PDF
                if asset_type_str == "pdf"
                else (SeedAssetType.IMAGE if asset_type_str == "image" else SeedAssetType.TEXT)
            )
            await insert_seed_asset(
                conn,
                generation_job_id=job_id,
                asset_type=asset_type,
                file_name=asset.file_name,
                storage_key=asset.storage_key,
                mime_type=asset.mime_type,
                content_text=asset.content_text,
                file_size_bytes=asset.file_size_bytes,
                checksum_sha256=asset.checksum_sha256,
            )

        # Persist / replace graph
        if job_type in ("regenerate_full", "regenerate"):
            await replace_map_graph(
                conn,
                map_id=map_id,
                source_job_id=job_id,
                nodes=node_records,
                edges=edge_records,
            )
        else:
            await persist_generated_graph(
                conn,
                map_id=map_id,
                source_job_id=job_id,
                nodes=node_records,
                edges=edge_records,
            )

        # Update terminal job status with guaranteed started_at (P1-15) and 100% progress
        await conn.execute(
            """
            UPDATE public.generation_jobs
               SET status = 'completed',
                   progress_percent = 100,
                   started_at = COALESCE(started_at, now()),
                   completed_at = now(),
                   error_message = NULL
             WHERE id = $1
            """,
            job_id,
        )

        # Insert assistant summary message in thread
        map_owner = await conn.fetchval("SELECT user_id FROM public.mind_maps WHERE id = $1", map_id)

        # Record a version-history snapshot of the just-completed graph (US-04 / S2-FE7).
        # nodes_raw / edges_raw are plain dicts (the worker's GenerationResult),
        # which compute_layout already mutated with pos_x/pos_y. Storing them
        # verbatim is what restore_map_graph_from_snapshot expects.
        snapshot_version = await next_snapshot_version(conn, map_id)
        snapshot_trigger_source = {
            "generate": "initial_generation",
            "regenerate_full": "full_regen",
            "regenerate_node": "branch_regen",
        }.get(str(job_type), "manual")
        await create_map_snapshot(
            conn,
            map_id=map_id,
            trigger_source=snapshot_trigger_source,
            trigger_job_id=job_id,
            snapshot_data={"nodes": nodes_raw, "edges": edges_raw},
            label=f"Version {snapshot_version}",
            created_by=map_owner,
        )

        if map_owner:
            thread_id = await get_or_create_chat_thread(conn, map_id, map_owner)
            await insert_chat_message(
                conn,
                thread_id,
                "assistant",
                f"Generated mind map with {len(node_records)} nodes and {len(edge_records)} edges.",
            )

    logger.info(
        "Job %s completed successfully: persisted %d nodes, %d edges",
        job_id,
        len(node_records),
        len(edge_records),
    )
    return {
        "status": "completed",
        "receipt_id": str(job_id),
        "already_processed": False,
        "nodes": len(node_records),
        "edges": len(edge_records),
    }


@router.post("/{job_id}/fail", status_code=200)
async def report_fail(
    job_id: UUID,
    result: GenerationResult,
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    authorization: str | None = Header(None),
):
    """Worker reports terminal failure or rejection with strict lifecycle constraint handling."""
    job_ctx = await verify_callback_auth(job_id, request, authorization, creds)
    pool = request.app.state.pool

    # Strict contract validation: assert identity and failed/rejected status
    if result.job_id != job_id:
        raise HTTPException(
            status_code=400,
            detail=f"job_id mismatch: URL is {job_id}, payload has {result.job_id}",
        )
    if str(job_ctx["map_id"]) != str(result.map_id):
        raise HTTPException(
            status_code=400,
            detail=f"map_id mismatch: job belongs to map {job_ctx['map_id']}, payload has {result.map_id}",
        )
    if result.status not in ("failed", "rejected"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{result.status}' for fail callback; expected 'failed' or 'rejected'",
        )

    # Sanitize error message to prevent leaking tracebacks, secrets, or internal paths
    raw_message = (result.error.message if result.error else "Generation failed") or "Generation failed"
    clean_message = raw_message.split("\n")[0].strip()[:250] or "Generation failed unexpectedly."

    async with transaction(pool) as conn:
        job_row = await conn.fetchrow(
            "SELECT id, status, started_at FROM public.generation_jobs WHERE id = $1 FOR UPDATE",
            job_id,
        )
        if not job_row:
            raise HTTPException(404, "Job not found")

        # Conflict: if job already completed, reject conflicting fail callback
        if job_row["status"] == "completed":
            logger.warning("Job %s already marked completed; rejecting conflicting fail callback.", job_id)
            raise HTTPException(
                status_code=409,
                detail="Cannot fail job: job has already reached terminal status 'completed'",
            )

        # Idempotency: if already failed or rejected, return 200 without re-writing
        if job_row["status"] in ("failed", "rejected"):
            logger.info(
                "Job %s already in terminal status %s; returning idempotent response.",
                job_id,
                job_row["status"],
            )
            return {
                "status": job_row["status"],
                "message": "Already processed",
                "receipt_id": str(job_id),
                "already_processed": True,
            }

        # P1-6: Honor chk_generation_job_lifecycle:
        # If job already started (started_at IS NOT NULL), terminal status must be 'failed'.
        # Only jobs that never started (started_at IS NULL) can be 'rejected'.
        final_status = "rejected" if result.status == "rejected" and job_row["started_at"] is None else "failed"

        try:
            await conn.execute(
                """
                UPDATE public.generation_jobs
                   SET status = $2::varchar,
                       started_at = CASE WHEN $2::varchar = 'rejected' THEN NULL ELSE COALESCE(started_at, now()) END,
                       completed_at = now(),
                       progress_percent = CASE WHEN $2::varchar = 'rejected' THEN 0 ELSE LEAST(progress_percent, 99) END,
                       error_message = $3::text
                 WHERE id = $1
                """,
                job_id,
                final_status,
                clean_message,
            )
        except Exception as db_exc:
            logger.exception("Database update failed during report_fail for job %s: %s", job_id, db_exc)
            raise HTTPException(status_code=500, detail=f"Database update failed: {db_exc}")

    logger.warning("Job %s marked %s: %s", job_id, final_status, clean_message)
    return {
        "status": final_status,
        "receipt_id": str(job_id),
        "already_processed": False,
    }


# ---------------------------------------------------------------------
# Internal Chat Completion Callbacks (S2-BE4 / US-15)
# ---------------------------------------------------------------------

chat_callback_router = APIRouter(prefix="/internal/chat", tags=["Internal Chat Callbacks"])


class ChatCompleteCallback(BaseModel):
    map_id: UUID
    job_id: UUID
    assistant_message: str = Field(..., description="Assistant response to persist to chat history")
    summary: str | None = Field(default=None, description="Updated rolling summary from worker if threshold reached")
    status: str = Field(default="completed", description="Job status: completed or failed")
    error: str | None = None


@chat_callback_router.post("/{job_id}/complete", status_code=200)
async def report_chat_complete(
    job_id: UUID,
    payload: ChatCompleteCallback,
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    authorization: str | None = Header(None),
):
    """Worker reports completion of chat response: persists assistant turn & updates rolling summary."""
    job_ctx = await verify_callback_auth(job_id, request, authorization, creds)
    pool = request.app.state.pool

    if payload.job_id != job_id:
        raise HTTPException(
            status_code=400,
            detail=f"job_id mismatch: URL is {job_id}, payload has {payload.job_id}",
        )
    if str(job_ctx["map_id"]) != str(payload.map_id):
        raise HTTPException(
            status_code=400,
            detail=f"map_id mismatch: job belongs to map {job_ctx['map_id']}, payload has {payload.map_id}",
        )

    map_id = job_ctx["map_id"]

    if payload.status == "failed":
        async with transaction(pool) as conn:
            await conn.execute(
                """
                UPDATE public.generation_jobs
                   SET status = 'failed',
                       completed_at = now(),
                       error_message = $2
                 WHERE id = $1
                """,
                job_id,
                payload.error or "Chat completion failed",
            )
        return {"status": "failed", "job_id": str(job_id), "map_id": str(map_id)}

    async with transaction(pool) as conn:
        job_row = await conn.fetchrow(
            "SELECT id, status FROM public.generation_jobs WHERE id = $1 FOR UPDATE",
            job_id,
        )
        if not job_row:
            raise HTTPException(404, "Job not found")

        if job_row["status"] == "completed":
            return {
                "status": "completed",
                "message": "Already processed",
                "receipt_id": str(job_id),
                "already_processed": True,
            }

        # 1. Fetch map owner and ensure chat thread exists
        map_owner = await conn.fetchval("SELECT user_id FROM public.mind_maps WHERE id = $1", map_id)
        if map_owner:
            thread_id = await get_or_create_chat_thread(conn, map_id, map_owner)
            # 2. Persist assistant turn
            if payload.assistant_message:
                await insert_chat_message(conn, thread_id, "assistant", payload.assistant_message)

            # 3. Update rolling summary in chat_threads if provided or threshold reached
            if payload.summary:
                await update_chat_thread_summary(conn, map_id, payload.summary)
            else:
                all_msgs = await get_chat_messages(conn, map_id)
                if len(all_msgs) >= 10:
                    from memory.summarizer import RollingSummarizer

                    summarizer = RollingSummarizer()
                    new_summary = await summarizer.generate_summary(all_msgs)
                    await update_chat_thread_summary(conn, map_id, new_summary)

        # 4. Update job status to completed
        await conn.execute(
            """
            UPDATE public.generation_jobs
               SET status = 'completed',
                   completed_at = now(),
                   progress_percent = 100
             WHERE id = $1
            """,
            job_id,
        )

    return {
        "status": "completed",
        "job_id": str(job_id),
        "map_id": str(map_id),
        "receipt_id": str(job_id),
        "already_processed": False,
        "summary_updated": bool(payload.summary),
    }
