from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.chat.dependencies import check_rate_limit, get_mindmap_service
from app.chat.schemas import (
    ActionConfirm,
    ActionConfirmResponse,
    MessageCreate,
    MessageResponse,
)
from app.chat.service import (
    confirm_proposed_action,
    get_paginated_history,
    save_message,
)
from app.core.supabase import get_current_user

if TYPE_CHECKING:
    from services.mindmap_service import MindMapBackendService

router = APIRouter(prefix="/chat", tags=["Chat API"])


@router.post("/messages", dependencies=[Depends(check_rate_limit)])
async def send_message(
    message: MessageCreate,
    current_user=Depends(get_current_user),
    mindmap_service: MindMapBackendService = Depends(get_mindmap_service),
) -> dict[str, Any]:
    """Send and persist a chat message, optionally triggering AI generation."""
    user_id = current_user.id
    map_id = UUID(message.map_id)

    # 1. Persist the chat turn in DB
    user_turn = save_message(
        user_id=str(user_id),
        message=message,
        role="user",
        proposed_action=message.proposed_action,
    )

    # 2. Trigger AI generation based on message topic/instruction
    try:
        result = await mindmap_service.generate(
            map_id=map_id,
            user_id=user_id,
            topic=message.content,
            detail_level="overview",
        )
        return {
            "status": "success",
            "message": "Chat message persisted and generation triggered.",
            "chat_turn": user_turn,
            "data": result,
        }
    except Exception:
        return {
            "status": "persisted",
            "message": "Chat message persisted successfully.",
            "chat_turn": user_turn,
            "detail": "AI generation failed due to an internal error.",
        }


@router.get("/history/{map_id}", response_model=list[MessageResponse])
async def get_history(
    map_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user=Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Retrieve paginated chat message history for the specified map."""
    user_id = str(current_user.id)
    history = get_paginated_history(map_id=map_id, user_id=user_id, limit=limit, offset=offset)
    return history


@router.post("/actions/{message_id}/confirm", response_model=ActionConfirmResponse | MessageResponse)
async def confirm_action(
    message_id: str,
    confirmation: ActionConfirm,
    current_user=Depends(get_current_user),
    mindmap_service: MindMapBackendService = Depends(get_mindmap_service),
):
    """Confirm or reject a proposed action attached to a chat message."""
    user_id = str(current_user.id)
    updated_message = confirm_proposed_action(message_id, user_id, confirmation)

    if not updated_message:
        raise HTTPException(
            status_code=404,
            detail="Chat message not found or unauthorized to confirm this action.",
        )

    # If action is confirmed and specifies regeneration, trigger mind map regeneration
    if confirmation.is_confirmed and updated_message.get("proposed_action"):
        action_data = updated_message["proposed_action"]
        map_id_str = updated_message.get("map_id")

        if action_data.get("action") == "regenerate" and map_id_str:
            instruction = action_data.get("instruction", "Regenerate based on previous context")
            try:
                result = await mindmap_service.regenerate_full(
                    map_id=UUID(map_id_str),
                    user_id=current_user.id,
                    instruction=instruction,
                )
                return ActionConfirmResponse(
                    message="Action confirmed and map regenerated.",
                    is_confirmed=True,
                    data=result,
                    updated_message=MessageResponse(**updated_message),
                )
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"Regeneration failed: {exc}")

    return ActionConfirmResponse(
        message="Action confirmation status updated.",
        is_confirmed=confirmation.is_confirmed,
        updated_message=MessageResponse(**updated_message),
    )
