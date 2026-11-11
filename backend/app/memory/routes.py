from __future__ import annotations

from typing import Any, cast

from core.supabase import get_current_user
from fastapi import APIRouter, Depends
from memory.schemas import CallbackPayload
from memory.service import MemoryService

router = APIRouter(prefix="/memory", tags=["Memory"])


@router.post("/context")
async def build_memory_context(
    map_id: str,
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    """Build and return memory context for a given mind map."""
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
async def completion_callback(payload: CallbackPayload) -> dict[str, Any]:
    """Internal completion callback — stores rolling summary if provided."""
    service = MemoryService()
    result = await service.handle_job_callback(payload)
    return result


@router.get("/summary/{map_id}")
async def get_map_summary(map_id: str, current_user=Depends(get_current_user)) -> dict[str, Any]:
    """Return the cached rolling summary for a mind map."""
    service = MemoryService()
    summary = service.get_summary(map_id)
    return {
        "map_id": map_id,
        "summary": summary,
        "has_summary": summary is not None,
    }
