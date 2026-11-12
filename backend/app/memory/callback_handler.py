from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from memory.schemas import CallbackPayload

logger = logging.getLogger(__name__)


class MemoryCallbackHandler:
    """Handles internal job-completion callbacks and caches rolling summaries."""

    _summary_cache: dict[str, str] = {}

    def __init__(self, db_admin=None) -> None:
        self._db_admin = db_admin

    async def handle_completion(self, payload: CallbackPayload) -> dict:
        """Process a completion callback payload and return an acknowledgement dict."""
        # Validate required fields
        if not payload.map_id or not isinstance(payload.map_id, str):
            return {"status": "error", "detail": "Invalid map_id"}
        if not payload.job_id or not isinstance(payload.job_id, str):
            return {"status": "error", "detail": "Invalid job_id"}

        if payload.status == "completed":
            action = "no_summary"
            if payload.summary:
                self._summary_cache[payload.map_id] = payload.summary
                action = "summary_stored"
            return {
                "status": "acknowledged",
                "map_id": payload.map_id,
                "job_id": payload.job_id,
                "action": action,
            }

        if payload.status == "failed":
            logger.error(
                "Memory callback: job %s for map %s failed — %s",
                payload.job_id,
                payload.map_id,
                payload.error or "(no error detail)",
            )
            return {
                "status": "acknowledged",
                "map_id": payload.map_id,
                "job_id": payload.job_id,
                "action": "failure_logged",
                "error": payload.error,
            }

        return {"status": "unknown_status", "map_id": payload.map_id}

    def get_cached_summary(self, map_id: str) -> str | None:
        """Return the cached rolling summary for a map, or None."""
        return self._summary_cache.get(map_id)

    def clear_summary(self, map_id: str) -> None:
        """Remove the cached summary for a map."""
        self._summary_cache.pop(map_id, None)


# ---------------------------------------------------------------------------
# Module-level FastAPI router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/memory", tags=["Memory"])


@router.post("/callback")
async def completion_callback(payload: CallbackPayload, request: Request):
    """Internal completion callback endpoint."""
    from core.supabase import admin  # noqa: PLC0415

    handler = MemoryCallbackHandler(admin)
    return await handler.handle_completion(payload)


@router.get("/summary/{map_id}")
async def get_summary(map_id: str, request: Request):
    """Get the cached rolling summary for a mind map (authenticated)."""
    from core.supabase import admin  # noqa: PLC0415

    handler = MemoryCallbackHandler(admin)
    summary = handler.get_cached_summary(map_id)
    return {
        "map_id": map_id,
        "summary": summary,
        "has_summary": summary is not None,
    }
