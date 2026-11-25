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
import os
from secrets import compare_digest
from typing import Any

from core.config import settings
from core.supabase import get_current_user
from fastapi import Depends, Header, HTTPException, Request, status

logger = logging.getLogger(__name__)


def require_admin_role(request: Request, user: Any = Depends(get_current_user)) -> Any:
    """Authorize administrators using the profile verified at authentication."""
    profile = getattr(request.state, "profile", {})
    if profile.get("role") != "admin" or profile.get("suspended_at") is not None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required.")
    return user


def internal_key_matches(expected: str | None, supplied: str | None) -> bool:
    return bool(
        expected
        and expected not in {"your-secure-internal-service-secret", "mindweave-dev-internal-secret"}
        and supplied
        and compare_digest(supplied.encode(), expected.encode())
    )


def require_internal_service(
    request: Request,
    x_internal_service_key: str | None = Header(None, alias="X-Internal-Service-Key"),
) -> dict[str, str]:
    """Authorize worker M2M calls strictly via pre-shared internal service secret (NEW-01, NEW-02).

    Admin sessions and user JWTs are explicitly FORBIDDEN from accessing internal memory
    endpoints, strictly satisfying the Sprint 3 DB2 privacy requirement.
    """
    expected_key = getattr(settings, "INTERNAL_SERVICE_KEY", None) or os.getenv("INTERNAL_SERVICE_KEY", "")
    if not internal_key_matches(expected_key, x_internal_service_key):
        logger.warning(
            "Unauthorized internal service request from %s",
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Valid internal service key required.",
        )
    return {"auth": "internal_service"}
