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

from auth.dependencies import require_admin_role
from core.supabase import admin
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from uploads.service import delete_user_storage_files

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


# ------------------------------------------------------------------ Dashboard
@router.get("/dashboard", status_code=200)
async def admin_dashboard(request: Request) -> dict[str, Any]:
    """Return aggregate platform statistics for the admin dashboard."""
    pool = _get_pool(request)

    total_users = await pool.fetchval("SELECT count(*)::int FROM public.profiles")
    total_maps = await pool.fetchval("SELECT count(*)::int FROM public.mind_maps")
    total_jobs = await pool.fetchval("SELECT count(*)::int FROM public.generation_jobs")
    failed_jobs = await pool.fetchval("SELECT count(*)::int FROM public.generation_jobs WHERE status = 'failed'")
    active_users_30d = await pool.fetchval(
        "SELECT count(DISTINCT user_id)::int FROM public.mind_maps WHERE updated_at >= now() - interval '30 days'"
    )

    return {
        "total_users": total_users or 0,
        "total_maps": total_maps or 0,
        "total_jobs": total_jobs or 0,
        "failed_jobs": failed_jobs or 0,
        "active_users_last_30_days": active_users_30d or 0,
    }


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
            p.id,
            u.email,
            p.created_at,
            CASE WHEN p.suspended_at IS NOT NULL THEN 'suspended' ELSE 'active' END AS status,
            (SELECT count(*)::int FROM public.mind_maps m WHERE m.user_id = p.id) AS map_count
        FROM public.profiles p
        LEFT JOIN auth.users u ON u.id = p.id
        ORDER BY p.created_at DESC
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

    existing = await pool.fetchrow("SELECT id FROM public.profiles WHERE id = $1", user_id)
    if not existing:
        raise HTTPException(404, "User not found")

    actor_id = getattr(admin_user, "id", None)
    if actor_id and str(actor_id) == str(user_id):
        raise HTTPException(400, "Cannot suspend your own admin account")

    await pool.execute(
        "UPDATE public.profiles SET suspended_at = now() WHERE id = $1",
        user_id,
    )

    await record_audit_log(
        pool=pool,
        actor_id=UUID(str(actor_id)) if actor_id else user_id,
        action="user.suspend",
        target_user_id=user_id,
        metadata={"target_user_id": str(user_id), "status": "suspended"},
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

    existing = await pool.fetchrow("SELECT id FROM public.profiles WHERE id = $1", user_id)
    if not existing:
        raise HTTPException(404, "User not found")

    actor_id = getattr(admin_user, "id", None)

    await pool.execute(
        "UPDATE public.profiles SET suspended_at = NULL WHERE id = $1",
        user_id,
    )

    await record_audit_log(
        pool=pool,
        actor_id=UUID(str(actor_id)) if actor_id else user_id,
        action="user.unsuspend",
        target_user_id=user_id,
        metadata={"target_user_id": str(user_id), "status": "active"},
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

    existing = await pool.fetchrow("SELECT id FROM public.profiles WHERE id = $1", user_id)
    if not existing:
        raise HTTPException(404, "User not found")

    actor_id = getattr(admin_user, "id", None)
    if actor_id and str(actor_id) == str(user_id):
        raise HTTPException(400, "Cannot delete your own admin account")

    # 1. Clean up associated Supabase Storage files
    try:
        await delete_user_storage_files(user_id=user_id, pool=pool)
    except Exception as exc:
        logger.warning("Storage cleanup failed for user %s: %s", user_id, exc)

    # 2. Immutable audit log: Record before deleting target database records
    await record_audit_log(
        pool=pool,
        actor_id=UUID(str(actor_id)) if actor_id else user_id,
        action="user.delete",
        target_user_id=user_id,
        metadata={"target_user_id": str(user_id), "action": "delete"},
    )

    # 3. Database cascade deletion
    await pool.execute("DELETE FROM public.profiles WHERE id = $1", user_id)

    # 4. Remove from Supabase Auth
    try:
        await asyncio.to_thread(admin.auth.admin.delete_user, str(user_id))
    except Exception as exc:
        logger.warning("Failed to delete user %s from Supabase auth: %s", user_id, exc)

    return {
        "message": "User and associated resources deleted successfully",
        "user_id": str(user_id),
    }


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
