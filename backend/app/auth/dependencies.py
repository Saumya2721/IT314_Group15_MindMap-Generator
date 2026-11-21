"""Admin authorization dependency for FastAPI.

Provides ``require_admin_role`` — a reusable dependency that verifies the
authenticated user holds the ``admin`` role in their profile before granting
access to protected admin endpoints.

Authentication is delegated to the existing ``get_current_user`` dependency
(JWT validation via Supabase).  Role verification uses a trusted database
lookup through the Supabase service-role client so the role value cannot be
forged by the caller.
"""

from __future__ import annotations

import logging
from typing import Any

from core.supabase import admin as supabase_admin
from core.supabase import get_current_user
from fastapi import Depends, HTTPException, status

logger = logging.getLogger(__name__)


def require_admin_role(
    user: Any = Depends(get_current_user),  # noqa: B008
) -> Any:
    """FastAPI dependency that enforces the ``admin`` role.

    1. Delegates authentication to ``get_current_user`` (401 on failure).
    2. Looks up ``profiles.role`` via the trusted Supabase service-role client.
    3. Returns the user object when ``role == 'admin'``.
    4. Raises HTTP 403 for any other role.
    """
    user_id = str(user.id)

    # Trusted server-side lookup — never rely on client-supplied role claims.
    try:
        res = supabase_admin.table("profiles").select("role, suspended_at").eq("id", user_id).execute()
        profile_data = res.data if res and hasattr(res, "data") else None
    except Exception as exc:
        logger.error("Failed to fetch profile for user %s: %s", user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unable to verify admin privileges.",
        )

    if not profile_data or len(profile_data) == 0:
        logger.warning("No profile found for user %s during admin check", user_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required.",
        )

    first_profile = profile_data[0]
    role = first_profile.get("role") if isinstance(first_profile, dict) else None
    suspended_at = first_profile.get("suspended_at") if isinstance(first_profile, dict) else None
    if role != "admin" or suspended_at is not None:
        logger.info(
            "Access denied to admin endpoint for user %s (role=%s, suspended=%s)",
            user_id,
            role,
            suspended_at is not None,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required.",
        )

    return user
