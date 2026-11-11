from typing import Any

from chat.schemas import ActionConfirm, MessageCreate
from core.supabase import admin

db = admin


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def get_or_create_thread(map_id: str, user_id: str) -> str:
    """Find existing chat thread for map or create one."""
    response = db.table("chat_threads").select("id").eq("map_id", map_id).execute()
    existing_threads = _as_dict_list(response.data)
    if existing_threads:
        return str(existing_threads[0]["id"])

    new_thread = db.table("chat_threads").insert({"map_id": map_id, "user_id": user_id}).execute()
    created_threads = _as_dict_list(new_thread.data)
    if created_threads:
        return str(created_threads[0]["id"])
    return ""


def save_message(
    user_id: str,
    message: MessageCreate,
    role: str = "user",
    proposed_action: dict[str, Any] | None = None,
    is_confirmed: bool = False,
) -> dict[str, Any]:
    """Persist chat message to chat_messages table under the map's chat thread."""
    thread_id = get_or_create_thread(message.map_id, user_id)
    payload = {
        "thread_id": thread_id,
        "role": role,
        "content": message.content,
        "proposed_action": proposed_action or message.proposed_action,
        "is_confirmed": is_confirmed,
    }
    response = db.table("chat_messages").insert(payload).execute()
    inserted_messages = _as_dict_list(response.data)
    msg_data = inserted_messages[0] if inserted_messages else payload
    msg_data["map_id"] = message.map_id
    msg_data["user_id"] = user_id
    return msg_data


def get_paginated_history(
    map_id: str,
    user_id: str,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Retrieve paginated chat message history for a given map thread."""
    thread_resp = db.table("chat_threads").select("id").eq("map_id", map_id).execute()
    thread_rows = _as_dict_list(thread_resp.data)
    if not thread_rows:
        return []

    thread_id = thread_rows[0]["id"]
    response = (
        db.table("chat_messages")
        .select("*")
        .eq("thread_id", thread_id)
        .order("created_at", desc=False)
        .range(offset, offset + limit - 1)
        .execute()
    )

    records = _as_dict_list(response.data)
    for item in records:
        item["map_id"] = map_id
        item["user_id"] = user_id
        if "is_confirmed" not in item:
            item["is_confirmed"] = False
        if "proposed_action" not in item:
            item["proposed_action"] = None
    return records


def confirm_proposed_action(
    message_id: str,
    user_id: str,
    confirmation: ActionConfirm,
) -> dict[str, Any] | None:
    """Update confirmation status on a chat message with a proposed action."""
    # Verify the message exists and belongs to a thread owned by the user
    thread_query = (
        db.table("chat_messages").select("id, thread_id, proposed_action, is_confirmed").eq("id", message_id).execute()
    )
    message_rows = _as_dict_list(thread_query.data)
    if not message_rows:
        return None

    msg_record = message_rows[0]
    thread_id = msg_record.get("thread_id")

    map_id = None
    if thread_id:
        t_resp = db.table("chat_threads").select("id, map_id, user_id").eq("id", thread_id).execute()
        thread_data_rows = _as_dict_list(t_resp.data)
        if thread_data_rows:
            thread_data = thread_data_rows[0]
            # Verify ownership if user_id is tracked on thread
            if thread_data.get("user_id") and str(thread_data.get("user_id")) != user_id:
                return None
            map_id = str(thread_data.get("map_id"))

    # Update confirmation in DB
    response = (
        db.table("chat_messages").update({"is_confirmed": confirmation.is_confirmed}).eq("id", message_id).execute()
    )

    updated_rows = _as_dict_list(response.data)
    updated = updated_rows[0] if updated_rows else msg_record
    updated["is_confirmed"] = confirmation.is_confirmed
    updated["map_id"] = map_id
    updated["user_id"] = user_id
    return updated
