from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from core.supabase import bearer, get_current_user
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from memory.schemas import CallbackPayload
from memory.service import MemoryService
from routes.internal import verify_callback_auth

from db.connection import transaction
from db.repository import assert_map_owner, update_chat_thread_summary

router = APIRouter(prefix="/memory", tags=["Memory"])


@router.post("/context")
async def build_memory_context(
    map_id: str,
    request: Request,
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    """Build and return memory context for a given mind map."""
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Database pool is not available")
    is_owner = await assert_map_owner(pool, UUID(map_id), current_user.id)
    if not is_owner:
        raise HTTPException(status_code=404, detail="Map not found")

    try:
        from core.supabase import admin  # noqa: PLC0415
    except ImportError:
        return {"map_id": map_id, "context": None, "message": "Supabase not configured"}

    thread_resp = admin.table("chat_threads").select("id, summary").eq("map_id", map_id).execute()
    t_data = cast(list[dict[str, Any]], thread_resp.data)
    if not t_data:
        return {"map_id": map_id, "context": None, "message": "No chat history found"}

    thread_id = str(t_data[0]["id"])
    thread_summary = str(t_data[0].get("summary") or "")

    msgs_resp = admin.table("chat_messages").select("*").eq("thread_id", thread_id).order("created_at").execute()
    raw_messages = [dict(m) for m in cast(list[dict[str, Any]], msgs_resp.data or [])]

    service = MemoryService()
    context = await service.build_context(map_id, raw_messages, existing_summary=thread_summary)

    return {
        "map_id": map_id,
        "total_tokens": context.total_tokens,
        "budget_remaining": context.budget_remaining,
        "is_truncated": context.is_truncated,
        "has_summary": context.summary is not None,
        "message_count": len(context.messages),
        "summary": context.summary,
        "formatted_context": service.format_context_for_llm(context),
    }


@router.post("/callback")
async def completion_callback(
    payload: CallbackPayload,
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Internal completion callback — securely verifies auth & stores rolling summary in PostgreSQL."""
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Database pool is not available")

    # C3: Verify internal callback authentication
    job_id = UUID(payload.job_id)
    await verify_callback_auth(job_id, request, authorization, creds)

    # C3: Persist summary durably in PostgreSQL chat_threads
    if payload.summary:
        async with transaction(pool) as conn:
            await update_chat_thread_summary(
                conn,
                map_id=UUID(payload.map_id),
                summary=payload.summary,
            )

    service = MemoryService()
    result = await service.handle_job_callback(payload)
    return result


@router.get("/summary/{map_id}")
async def get_map_summary(
    map_id: str,
    request: Request,
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    """Return the rolling summary for a mind map."""
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Database pool is not available")

    is_owner = await assert_map_owner(pool, UUID(map_id), current_user.id)
    if not is_owner:
        raise HTTPException(status_code=404, detail="Map not found")

    summary = await pool.fetchval("SELECT summary FROM public.chat_threads WHERE map_id = $1", UUID(map_id))
    return {
        "map_id": map_id,
        "summary": summary,
        "has_summary": summary is not None,
    }
