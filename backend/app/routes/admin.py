"""Admin API routes — platform oversight endpoints.

Every endpoint in this router requires the ``admin`` role, enforced at the
router level via the ``require_admin_role`` dependency so that all current
**and future** endpoints automatically inherit the protection.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from auth.dependencies import require_admin_role
from fastapi import APIRouter, Depends, HTTPException, Request

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
async def list_users(request: Request) -> list[dict[str, Any]]:
    """List all user profiles (admin oversight)."""
    pool = _get_pool(request)
    rows = await pool.fetch(
        "SELECT id, name, role, suspended_at, created_at FROM public.profiles ORDER BY created_at DESC"
    )
    return [dict(r) for r in rows]


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
    return dict(row)


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
