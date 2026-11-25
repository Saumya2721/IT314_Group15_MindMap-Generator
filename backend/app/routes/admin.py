"""Admin API routes — platform oversight endpoints.

Every endpoint in this router requires the ``admin`` role, enforced at the
router level via the ``require_admin_role`` dependency so that all current
**and future** endpoints automatically inherit the protection.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from uuid import UUID

from arq.jobs import Job
from auth.dependencies import require_admin_role
from core.supabase import admin
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from services.pipeline_config import PipelineConfig, PipelineConfigPatch, effective_config, read_config_overrides
from services.queue import get_redis_pool
from uploads.service import delete_user_storage_files

from db.connection import transaction

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
    dependencies=[Depends(require_admin_role)],
)


def _get_pool(request: Request):
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(503, "Database pool not initialized")
    return pool


async def record_audit_log(
    pool: Any,
    actor_id: UUID | str,
    action: str,
    target_user_id: UUID | str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Record an immutable audit log entry in public.admin_audit_log."""
    actor_uuid = UUID(str(actor_id)) if actor_id else None
    target_uuid = UUID(str(target_user_id)) if target_user_id else None
    meta_json = json.dumps(metadata or {})
    try:
        await pool.execute(
            """
            INSERT INTO public.admin_audit_log (actor_id, action, target_user_id, metadata, created_at)
            VALUES ($1, $2, $3, $4::jsonb, now())
            """,
            actor_uuid,
            action,
            target_uuid,
            meta_json,
        )
    except Exception as exc:
        logger.error(
            "Failed to write admin audit log for actor=%s action=%s target=%s: %s",
            actor_id,
            action,
            target_user_id,
            exc,
        )
        raise HTTPException(500, "Failed to record audit log")


@router.get("/config")
async def get_pipeline_config(request: Request) -> PipelineConfig:
    return effective_config(await read_config_overrides(_get_pool(request)))


@router.patch("/config")
async def update_pipeline_config(
    patch: PipelineConfigPatch,
    request: Request,
    admin_user: Any = Depends(require_admin_role),
) -> PipelineConfig:
    changes = patch.model_dump(exclude_unset=True)
    async with transaction(_get_pool(request)) as conn:
        overrides = await conn.fetchval(
            """
            UPDATE public.pipeline_config
            SET settings = settings || $1::jsonb
            WHERE singleton
            RETURNING settings
            """,
            json.dumps(changes),
        )
        if overrides is None:
            raise HTTPException(503, "Pipeline configuration is not initialized")
        updated = effective_config(overrides)
        await record_audit_log(
            conn,
            actor_id=admin_user.id,
            action="pipeline_config.update",
            metadata={"changes": changes},
        )
    return updated


# ------------------------------------------------------------------ Dashboard
@router.get("/dashboard", status_code=200)
async def admin_dashboard(request: Request, admin_user: Any = Depends(require_admin_role)) -> dict[str, Any]:
    """Return aggregate platform statistics strictly from aggregate database views (AUD-06 / S3-BE3).

    Privacy & Security Invariant: Never queries raw application tables (profiles, mind_maps, generation_jobs).
    """
    pool = _get_pool(request)

    try:
        async with transaction(pool) as conn:
            await conn.execute("SELECT set_config('request.jwt.claim.sub', $1, true)", str(admin_user.id))
            user_row = await conn.fetchrow(
                "SELECT total_users, active_users_last_30_days FROM public.admin_active_users"
            )
            gen_row = await conn.fetchrow(
                "SELECT total_generations, maps_generated_last_30_days, total_maps FROM public.admin_generation_counts"
            )
            failure_row = await conn.fetchrow("SELECT failed_jobs FROM public.admin_generation_failure_rates")

        user_dict = dict(user_row) if user_row else {}
        gen_dict = dict(gen_row) if gen_row else {}
        failure_dict = dict(failure_row) if failure_row else {}

        return {
            "total_users": int(user_dict.get("total_users") or 0),
            "total_maps": int(gen_dict.get("total_maps") or 0),
            "total_jobs": int(gen_dict.get("total_generations") or 0),
            "failed_jobs": int(failure_dict.get("failed_jobs") or 0),
            "active_users_last_30_days": int(user_dict.get("active_users_last_30_days") or 0),
        }
    except Exception as exc:
        logger.error("Failed to query aggregate views in admin_dashboard: %s", exc)
        raise HTTPException(500, "Failed to retrieve dashboard aggregate analytics") from exc


# ------------------------------------------------------------------ User management
@router.get("/users", status_code=200)
async def list_users(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    """List user metadata with pagination (admin oversight).

    Returns id, email, created_at, status, and map_count.
    Never exposes user content fields.
    """
    pool = _get_pool(request)
    query = """
        SELECT
            COALESCE(p.id, d.user_id) AS id,
            u.email,
            COALESCE(p.created_at, d.created_at) AS created_at,
            CASE
                WHEN d.user_id IS NOT NULL THEN 'deletion_pending'
                WHEN p.suspended_at IS NOT NULL THEN 'suspended'
                ELSE 'active'
            END AS status,
            (SELECT count(*)::int FROM public.mind_maps m WHERE m.user_id = p.id) AS map_count
        FROM public.profiles p
        FULL JOIN (SELECT user_id, created_at FROM public.account_deletion_requests WHERE completed_at IS NULL) d
            ON d.user_id = p.id
        LEFT JOIN auth.users u ON u.id = COALESCE(p.id, d.user_id)
        ORDER BY created_at DESC, id
        LIMIT $1 OFFSET $2
    """
    rows = await pool.fetch(query, limit, offset)
    results: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        created_at_val = d.get("created_at")
        if created_at_val is not None and hasattr(created_at_val, "isoformat"):
            created_at_str: str = created_at_val.isoformat()
        else:
            created_at_str = str(created_at_val or "")
        results.append(
            {
                "id": str(d["id"]),
                "email": d.get("email"),
                "created_at": created_at_str,
                "status": d.get("status", "active"),
                "map_count": d.get("map_count") or 0,
            }
        )
    return results


@router.get("/users/{user_id}", status_code=200)
async def get_user(user_id: UUID, request: Request) -> dict[str, Any]:
    """Retrieve a single user profile."""
    pool = _get_pool(request)
    row = await pool.fetchrow(
        "SELECT id, name, role, suspended_at, created_at FROM public.profiles WHERE id = $1",
        user_id,
    )
    if not row:
        raise HTTPException(404, "User not found")
    d = dict(row)
    d["status"] = "suspended" if d.get("suspended_at") else "active"
    return d


@router.post("/users/{user_id}/suspend", status_code=200)
async def suspend_user(
    user_id: UUID,
    request: Request,
    admin_user: Any = Depends(require_admin_role),
) -> dict[str, Any]:
    """Suspend the specified user."""
    pool = _get_pool(request)

    actor_id = UUID(str(admin_user.id))
    if actor_id == user_id:
        raise HTTPException(400, "Cannot suspend your own admin account")
    async with transaction(pool) as conn:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext('account_delete:' || $1::text))", str(user_id))
        if await conn.fetchval("SELECT 1 FROM public.account_deletion_requests WHERE user_id = $1", user_id):
            raise HTTPException(409, "Account deletion is already in progress")
        updated = await conn.fetchval(
            "UPDATE public.profiles SET suspended_at = COALESCE(suspended_at, now()) WHERE id = $1 RETURNING id",
            user_id,
        )
        if updated is None:
            raise HTTPException(404, "User not found")
        await record_audit_log(
            conn, actor_id=actor_id, action="user.suspend", target_user_id=user_id, metadata={"status": "suspended"}
        )

    return {
        "message": "User suspended successfully",
        "user_id": str(user_id),
        "status": "suspended",
    }


@router.post("/users/{user_id}/unsuspend", status_code=200)
async def unsuspend_user(
    user_id: UUID,
    request: Request,
    admin_user: Any = Depends(require_admin_role),
) -> dict[str, Any]:
    """Restore access to a suspended user."""
    pool = _get_pool(request)

    actor_id = UUID(str(admin_user.id))
    async with transaction(pool) as conn:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext('account_delete:' || $1::text))", str(user_id))
        if await conn.fetchval("SELECT 1 FROM public.account_deletion_requests WHERE user_id = $1", user_id):
            raise HTTPException(409, "Account deletion is already in progress")
        updated = await conn.fetchval(
            "UPDATE public.profiles SET suspended_at = NULL WHERE id = $1 RETURNING id", user_id
        )
        if updated is None:
            raise HTTPException(404, "User not found")
        await record_audit_log(
            conn, actor_id=actor_id, action="user.unsuspend", target_user_id=user_id, metadata={"status": "active"}
        )

    return {
        "message": "User unsuspended successfully",
        "user_id": str(user_id),
        "status": "active",
    }


@router.delete("/users/{user_id}", status_code=200)
async def delete_user(
    user_id: UUID,
    request: Request,
    admin_user: Any = Depends(require_admin_role),
) -> dict[str, Any]:
    """Permanently delete a user account with cascade cleanup of storage and database records."""
    pool = _get_pool(request)

    actor_id = UUID(str(admin_user.id))
    if actor_id == user_id:
        raise HTTPException(400, "Cannot delete your own admin account")

    async with transaction(pool) as conn:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext('account_delete:' || $1::text))", str(user_id))
        operation = await conn.fetchrow("SELECT * FROM public.account_deletion_requests WHERE user_id = $1", user_id)
        if operation is None:
            suspended = await conn.fetchval(
                "UPDATE public.profiles SET suspended_at = COALESCE(suspended_at, now()) WHERE id = $1 RETURNING id",
                user_id,
            )
            if suspended is None:
                raise HTTPException(404, "User not found")
            await conn.execute(
                """
                INSERT INTO public.account_deletion_requests (user_id, actor_id, map_ids)
                VALUES ($1, $2, ARRAY(SELECT id FROM public.mind_maps WHERE user_id = $1))
                """,
                user_id,
                actor_id,
            )
            await record_audit_log(conn, actor_id=actor_id, action="user.delete.requested", target_user_id=user_id)

    phase = "storage cleanup"
    try:
        async with transaction(pool) as conn:
            operation = await conn.fetchrow(
                "SELECT * FROM public.account_deletion_requests WHERE user_id = $1 FOR UPDATE", user_id
            )
            if operation["storage_cleaned_at"] is None:
                if operation.get("map_ids") and await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM public.generation_jobs
                        WHERE map_id = ANY($1::uuid[]) AND status IN ('pending', 'running')
                    )
                    """,
                    operation["map_ids"],
                ):
                    return {
                        "success": False,
                        "user_id": str(user_id),
                        "message": "Deletion is waiting for active jobs. Retry after they finish.",
                    }
                await delete_user_storage_files(user_id=user_id, pool=conn)
                await conn.execute(
                    "UPDATE public.account_deletion_requests SET storage_cleaned_at = now() WHERE user_id = $1",
                    user_id,
                )

        phase = "vector cleanup"
        async with transaction(pool) as conn:
            operation = await conn.fetchrow(
                "SELECT * FROM public.account_deletion_requests WHERE user_id = $1 FOR UPDATE", user_id
            )
            if operation.get("map_ids") and operation["vectors_cleaned_at"] is None:
                redis = get_redis_pool(request)
                if redis is None:
                    raise RuntimeError("Queue unavailable")
                job_id = f"account-vectors:{user_id}:{operation['vector_attempt']}"
                await redis.enqueue_job(
                    "delete_account_map_vectors", [str(m) for m in operation["map_ids"]], _job_id=job_id
                )
                result = await Job(job_id, redis=redis).result_info()
                if result is None or not result.success:
                    if result is not None:
                        await conn.execute(
                            "UPDATE public.account_deletion_requests SET vector_attempt = vector_attempt + 1 "
                            "WHERE user_id = $1",
                            user_id,
                        )
                    return {
                        "success": False,
                        "user_id": str(user_id),
                        "message": "Vector cleanup is pending. Retry deletion to check its progress.",
                    }
                await conn.execute(
                    "UPDATE public.account_deletion_requests SET vectors_cleaned_at = now() WHERE user_id = $1",
                    user_id,
                )

        phase = "account removal"
        async with transaction(pool) as conn:
            operation = await conn.fetchrow(
                "SELECT * FROM public.account_deletion_requests WHERE user_id = $1 FOR UPDATE", user_id
            )
            if operation["completed_at"] is None:
                auth_exists = await conn.fetchval("SELECT EXISTS(SELECT 1 FROM auth.users WHERE id = $1)", user_id)
                if auth_exists:
                    await asyncio.to_thread(admin.auth.admin.delete_user, str(user_id))
                await conn.execute("DELETE FROM public.profiles WHERE id = $1", user_id)
                await record_audit_log(
                    conn, actor_id=operation["actor_id"], action="user.delete", target_user_id=user_id
                )
                await conn.execute(
                    "UPDATE public.account_deletion_requests SET completed_at = now() WHERE user_id = $1", user_id
                )
    except Exception:
        logger.exception("Account deletion paused for %s during %s", user_id, phase)
        return {
            "success": False,
            "user_id": str(user_id),
            "message": f"Deletion paused during {phase}. Retry to resume.",
        }

    return {"success": True, "user_id": str(user_id), "message": "User and associated resources deleted successfully"}


# ------------------------------------------------------------------ Audit log
@router.get("/audit-log", status_code=200)
async def list_audit_log(request: Request, limit: int = 50) -> list[dict[str, Any]]:
    """Return recent entries from the admin audit log."""
    pool = _get_pool(request)
    clamped_limit = max(1, min(limit, 200))
    rows = await pool.fetch(
        "SELECT id, actor_id, action, target_user_id, metadata, created_at "
        "FROM public.admin_audit_log ORDER BY created_at DESC LIMIT $1",
        clamped_limit,
    )
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ Aggregate Analytics
@router.get("/stats", status_code=200)
async def admin_stats(request: Request, admin_user: Any = Depends(require_admin_role)) -> dict[str, Any]:
    """Return aggregate operational analytics for platform administrators.

    Privacy & Security Invariants:
    - Queries STRICTLY against aggregate database views (public.admin_*).
    - NEVER queries raw content tables (mind_maps, nodes, edges, chat_messages, agent_memories).
    - Exposes zero user-generated content or sensitive user information.
    - Protected by require_admin_role at the router level.
    """
    pool = _get_pool(request)

    try:
        async with transaction(pool) as conn:
            await conn.execute("SELECT set_config('request.jwt.claim.sub', $1, true)", str(admin_user.id))
            user_row = await conn.fetchrow(
                "SELECT total_users, active_users_last_7_days, active_users_last_30_days FROM public.admin_active_users"
            )
            gen_row = await conn.fetchrow(
                "SELECT total_generations, generations_last_7_days, generations_last_30_days, "
                "maps_generated_last_7_days, maps_generated_last_30_days, maps_created_per_day "
                "FROM public.admin_generation_counts"
            )
            failure_row = await conn.fetchrow(
                "SELECT failed_jobs, completed_jobs, failure_rate FROM public.admin_generation_failure_rates"
            )
            error_rows = await conn.fetch("SELECT category, count FROM public.admin_job_error_categories")
            perf_row = await conn.fetchrow(
                "SELECT avg_generation_latency, avg_chat_turn_latency FROM public.admin_performance_latencies"
            )

    except Exception as exc:
        logger.error("Failed to query aggregate admin analytics views: %s", exc)
        raise HTTPException(500, "Failed to retrieve aggregate operational analytics") from exc

    # Parse user metrics
    user_data = dict(user_row) if user_row else {}
    total_users = int(user_data.get("total_users") or 0)
    active_7d = int(user_data.get("active_users_last_7_days") or 0)
    active_30d = int(user_data.get("active_users_last_30_days") or 0)

    # Parse map generation metrics
    gen_data = dict(gen_row) if gen_row else {}
    maps_7d = int(gen_data.get("maps_generated_last_7_days") or 0)
    maps_30d = int(gen_data.get("maps_generated_last_30_days") or 0)
    total_gen = int(gen_data.get("total_generations") or 0)

    # Parse failure metrics
    failure_data = dict(failure_row) if failure_row else {}
    failure_rate = float(failure_data.get("failure_rate") or 0.0)
    failed_jobs = int(failure_data.get("failed_jobs") or 0)
    completed_jobs = int(failure_data.get("completed_jobs") or 0)

    # Parse error categories breakdown
    error_categories: dict[str, int] = {}
    if error_rows:
        for r in error_rows:
            d = dict(r)
            cat = str(d.get("category") or "unknown")
            count = int(d.get("count") or 0)
            error_categories[cat] = count

    # Parse performance latencies
    perf_data = dict(perf_row) if perf_row else {}
    avg_gen_latency = float(
        perf_data.get("avg_generation_latency") or perf_data.get("avg_generation_latency_seconds") or 0.0
    )
    avg_chat_latency = float(
        perf_data.get("avg_chat_turn_latency") or perf_data.get("avg_chat_turn_latency_seconds") or 0.0
    )

    return {
        "users": {
            "total_users": total_users,
            "active_users_last_7_days": active_7d,
            "active_users_last_30_days": active_30d,
        },
        "map_generation": {
            "maps_generated_last_7_days": maps_7d,
            "maps_generated_last_30_days": maps_30d,
            "total_generations": total_gen,
            "maps_created_per_day": gen_data.get("maps_created_per_day") or [],
        },
        "job_failures": {
            "failure_rate": failure_rate,
            "failed_jobs": failed_jobs,
            "completed_jobs": completed_jobs,
            "error_categories": error_categories,
        },
        "performance": {
            "avg_generation_latency": avg_gen_latency,
            "avg_generation_latency_seconds": avg_gen_latency,
            "avg_chat_turn_latency": avg_chat_latency,
            "avg_chat_turn_latency_seconds": avg_chat_latency,
        },
        # Flat aliases for direct field access
        "total_users": total_users,
        "active_users_last_7_days": active_7d,
        "active_users_last_30_days": active_30d,
        "maps_generated_last_7_days": maps_7d,
        "maps_generated_last_30_days": maps_30d,
        "failure_rate": failure_rate,
        "error_categories": error_categories,
        "avg_generation_latency": avg_gen_latency,
        "avg_generation_latency_seconds": avg_gen_latency,
        "avg_chat_turn_latency": avg_chat_latency,
        "avg_chat_turn_latency_seconds": avg_chat_latency,
    }
