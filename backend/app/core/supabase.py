"""Supabase client management and authentication dependencies.

Replaces the legacy mm_backend.supa implementation with robust error
handling and lazy initialization to avoid crashing on import when
environment variables are not yet loaded.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from uuid import UUID

from dotenv import find_dotenv, load_dotenv
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from supabase import Client, create_client

logger = logging.getLogger(__name__)

# Search for .env in current dir, parent dir, and sibling llm dir
backend_root = Path(__file__).resolve().parent.parent
project_root = backend_root.parent
for env_candidate in (
    backend_root / ".env",
    project_root / ".env",
    project_root / "llm" / ".env",
):
    if env_candidate.exists():
        load_dotenv(env_candidate)

load_dotenv(find_dotenv())

SUPABASE_URL = re.sub(r"/rest/v1/?$", "", (os.getenv("SUPABASE_URL") or "").strip()).rstrip("/")
SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")
ANON_KEY = os.getenv("SUPABASE_ANON_KEY")

_admin_client: Client | None = None


def is_supabase_configured() -> bool:
    """Check if valid Supabase connection settings are present in the environment."""
    return bool(SUPABASE_URL and (SERVICE_KEY or ANON_KEY))


def get_admin_client() -> Client:
    """Lazily initialize the Supabase Admin Client.

    Raises HTTPException or RuntimeError with a clear message if unconfigured.
    """
    global _admin_client
    if _admin_client is not None:
        return _admin_client

    if not SUPABASE_URL or not SERVICE_KEY:
        raise RuntimeError(
            "Supabase admin credentials missing. Please set SUPABASE_URL and "
            "SUPABASE_SERVICE_ROLE_KEY in your .env file."
        )

    _admin_client = create_client(SUPABASE_URL, SERVICE_KEY)
    return _admin_client


class _AdminProxy:
    """Proxy object allowing `admin.table(...)` attribute access with lazy initialization."""

    def __getattr__(self, name: str):
        client = get_admin_client()
        return getattr(client, name)


# Exported admin client proxy (never logs users in; for backend storage/db operations only)
admin: Client = _AdminProxy()  # type: ignore[assignment]


def anon_client() -> Client:
    """Create a fresh anon-key client per auth call so sessions never leak between requests."""
    if not SUPABASE_URL or not ANON_KEY:
        raise HTTPException(
            status_code=500,
            detail="Supabase auth credentials missing. Please set SUPABASE_URL and SUPABASE_ANON_KEY in your .env file.",
        )
    return create_client(SUPABASE_URL, ANON_KEY)


bearer = HTTPBearer(auto_error=False)


def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
):
    """Validate token from Authorization header OR HttpOnly cookie via Supabase auth."""
    token: str | None = None
    if creds and creds.credentials:
        token = creds.credentials
    elif request.cookies.get("access_token"):
        token = request.cookies.get("access_token")

    if not token:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not is_supabase_configured():
        raise HTTPException(
            status_code=500,
            detail="Supabase authentication is not configured on this server.",
        )

    try:
        res = anon_client().auth.get_user(token)
        user = res.user if res else None
    except Exception as exc:
        logger.warning("Supabase token verification failed: %s", exc)
        user = None

    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def current_user_id(user=Depends(get_current_user)) -> UUID:
    """Extract and validate the UUID of the authenticated user."""
    return UUID(user.id)
