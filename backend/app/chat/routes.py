from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from chat.dependencies import check_rate_limit, get_mindmap_service
from chat.schemas import (
    ActionConfirm,
    ActionConfirmResponse,
    MessageCreate,
    MessageResponse,
)
from chat.service import (
    confirm_proposed_action,
    get_paginated_history,
    save_message,
)
from contracts.models import ChatTurnRequest, JobType
from core.config import settings
from core.supabase import get_current_user
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from services.queue import enqueue_chat_turn_job

from db.connection import transaction
from db.repository import (
    assert_map_owner,
    create_generation_job,
    list_map_edges,
    list_map_nodes,
)

if TYPE_CHECKING:
    from services.mindmap_service import MindMapBackendService

logger = logging.getLogger(__name__)


def _safe_parse_json_dict(val: Any) -> dict[str, Any]:
    if not val:
        return {}
    while isinstance(val, str):
        try:
            val = json.loads(val)
        except Exception:
            return {}
    if hasattr(val, "keys"):
        return dict(val)
    if isinstance(val, dict):
        return val
    return {}


def _safe_parse_proposed_action(val: Any) -> dict[str, Any] | None:
    if not val:
        return None
    while isinstance(val, str):
        try:
            val = json.loads(val)
        except Exception:
            return None
    if hasattr(val, "keys"):
        return dict(val)
    if isinstance(val, dict):
        return val
    return None


def _format_msg_dict(row: Any, map_id: UUID | str) -> dict[str, Any]:
    row_dict = dict(row) if hasattr(row, "keys") else (row if isinstance(row, dict) else {})
    pa = _safe_parse_proposed_action(row_dict.get("proposed_action"))
    meta = _safe_parse_json_dict(row_dict.get("metadata"))

    raw_conf = row_dict.get("is_confirmed")
    exec_status = meta.get("execution_status")
    if pa is not None:
        if raw_conf is True:
            is_confirmed = True
        elif exec_status == "dismissed":
            is_confirmed = False
        else:
            is_confirmed = None
    else:
        is_confirmed = bool(raw_conf) if raw_conf is not None else False

    return {
        "id": str(row_dict.get("id")),
        "map_id": str(map_id),
        "user_id": str(row_dict.get("user_id")) if row_dict.get("user_id") else None,
        "role": row_dict.get("role", "assistant"),
        "content": row_dict.get("content", ""),
        "proposed_action": pa,
        "is_confirmed": is_confirmed,
        "metadata": meta,
        "created_at": row_dict.get("created_at") or datetime.now(UTC),
    }


router = APIRouter(prefix="/chat", tags=["Chat API"])


@router.post("/messages", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(check_rate_limit)])
async def send_message(
    message: MessageCreate,
    request: Request,
    current_user=Depends(get_current_user),
    mindmap_service: MindMapBackendService = Depends(get_mindmap_service),
) -> dict[str, Any]:
    """Send and persist a chat message, enqueuing AI chat turn task for conversational assistance."""
    user_id = str(current_user.id)
    user_uuid = UUID(user_id)
    map_id = UUID(str(message.map_id))

    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Database pool is not available")

    is_owner = await assert_map_owner(pool, map_id, user_uuid)
    if not is_owner:
        raise HTTPException(status_code=404, detail="Map not found")

    redis_pool = getattr(request.app.state, "redis_pool", None)
    if redis_pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Chat processing queue is currently unavailable. Please try again.",
        )

    target_nid = UUID(message.target_node_id) if message.target_node_id else None
    if target_nid:
        node_exists = await pool.fetchval(
            "SELECT 1 FROM public.nodes WHERE id = $1 AND map_id = $2",
            target_nid,
            map_id,
        )
        if not node_exists:
            raise HTTPException(status_code=404, detail="Target node not found on this map")

    # 1. Persist the chat turn in DB
    user_turn = save_message(
        user_id=str(user_id),
        message=message,
        role="user",
        proposed_action=message.proposed_action,
    )

    job_id = uuid4()
    callback_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(callback_token.encode("utf-8")).hexdigest()

    msg_id = UUID(str(user_turn["id"])) if user_turn.get("id") else None

    try:
        async with transaction(pool) as conn:
            await create_generation_job(
                conn,
                map_id=map_id,
                job_type=JobType.CHAT_TURN,
                job_id=job_id,
                triggered_by_message_id=msg_id,
                target_node_id=target_nid,
                callback_token_hash=token_hash,
            )

            # Assemble current map graph snapshot and thread context
            nodes_rows = await list_map_nodes(conn, map_id)
            edges_rows = await list_map_edges(conn, map_id)
            map_snapshot = {
                "map_id": str(map_id),
                "nodes": [dict(n) for n in nodes_rows],
                "edges": [dict(e) for e in edges_rows],
            }
            thread_id_val = await conn.fetchval("SELECT id FROM public.chat_threads WHERE map_id = $1", map_id)
            thread_summary = None
            summary_cursor = None
            history: list[dict[str, Any]] = []
            if thread_id_val:
                cursor_row = await conn.fetchrow(
                    "SELECT summary, summary_through_message_id FROM public.chat_threads WHERE id = $1",
                    thread_id_val,
                )
                if cursor_row:
                    raw_summary = cursor_row.get("summary")
                    thread_summary = str(raw_summary) if raw_summary is not None else None
                    summary_cursor = cursor_row.get("summary_through_message_id")

                # C5 & C7: Query history strictly bounded by summary cursor and excluding just-created user message
                if summary_cursor:
                    msg_rows = await conn.fetch(
                        """
                        SELECT role, content
                          FROM public.chat_messages
                         WHERE thread_id = $1
                           AND ($2::uuid IS NULL OR id != $2)
                           AND created_at > (SELECT created_at FROM public.chat_messages WHERE id = $3)
                         ORDER BY created_at DESC, id DESC
                         LIMIT 10
                        """,
                        thread_id_val,
                        msg_id,
                        summary_cursor,
                    )
                else:
                    msg_rows = await conn.fetch(
                        """
                        SELECT role, content
                          FROM public.chat_messages
                         WHERE thread_id = $1
                           AND ($2::uuid IS NULL OR id != $2)
                         ORDER BY created_at DESC, id DESC
                         LIMIT 10
                        """,
                        thread_id_val,
                        msg_id,
                    )
                history = [{"role": r["role"], "content": r["content"]} for r in reversed(msg_rows)]

        memory_ctx = {
            "summary": thread_summary,
            "summary_through_message_id": str(summary_cursor) if summary_cursor else None,
        }

        chat_req = ChatTurnRequest(
            job_id=job_id,
            map_id=map_id,
            job_type=JobType.CHAT_TURN,
            message=message.content,
            thread_id=thread_id_val,
            target_node_id=target_nid,
            history=history,
            summary=thread_summary,
            rolling_summary=thread_summary,
            map_snapshot=map_snapshot,
            memory_context=memory_ctx,
            callback_base_url=settings.BACKEND_INTERNAL_URL,
            callback_token=callback_token,
        )

        await enqueue_chat_turn_job(redis_pool, chat_req.model_dump(mode="json"))
        return {
            "status": "accepted",
            "job_id": str(job_id),
            "message": "Chat message persisted and generation triggered.",
            "chat_turn": user_turn,
            "poll": f"/api/maps/{map_id}/jobs/{job_id}",
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to dispatch chat turn task for job %s: %s", job_id, exc)
        if pool:
            try:
                await pool.execute(
                    """
                    UPDATE public.generation_jobs
                       SET status = 'rejected',
                           completed_at = now(),
                           progress_percent = 0,
                           error_message = $2
                     WHERE id = $1 AND started_at IS NULL
                    """,
                    job_id,
                    f"Queue dispatch failed: {exc}",
                )
            except Exception as db_exc:
                logger.warning("Failed to mark job %s rejected: %s", job_id, db_exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Chat generation service is unavailable: {exc}",
        )


@router.get("/history/{map_id}", response_model=list[MessageResponse])
async def get_history(
    map_id: str,
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user=Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Retrieve paginated chat message history for the specified map."""
    user_id = str(current_user.id)
    user_uuid = UUID(user_id)
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Database pool is not available")
    is_owner = await assert_map_owner(pool, UUID(str(map_id)), user_uuid)
    if not is_owner:
        raise HTTPException(status_code=404, detail="Map not found")
    history = get_paginated_history(map_id=map_id, user_id=user_id, limit=limit, offset=offset)
    return history


@router.post("/actions/{message_id}/confirm", response_model=ActionConfirmResponse)
async def confirm_action(
    message_id: str,
    confirmation: ActionConfirm,
    request: Request,
    current_user=Depends(get_current_user),
    mindmap_service: MindMapBackendService = Depends(get_mindmap_service),
):
    """Confirm or reject a proposed action attached to a chat message, executing the operation on success."""
    user_id = str(current_user.id)
    user_uuid = UUID(user_id)
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Database pool is not available")

    msg_record = await pool.fetchrow(
        """
        SELECT m.user_id, ct.map_id, cm.id, cm.role, cm.content, cm.proposed_action, cm.is_confirmed, cm.metadata, cm.created_at
          FROM public.chat_messages cm
          JOIN public.chat_threads ct ON ct.id = cm.thread_id
          JOIN public.mind_maps m ON m.id = ct.map_id
         WHERE cm.id = $1
        """,
        UUID(message_id),
    )
    if not msg_record or str(msg_record["user_id"]) != user_id:
        raise HTTPException(status_code=404, detail="Chat message not found or unauthorized.")

    map_id = msg_record["map_id"]
    action_data = _safe_parse_proposed_action(msg_record["proposed_action"]) or {}
    meta = _safe_parse_json_dict(msg_record.get("metadata"))
    current_status = meta.get("execution_status")

    # 1. Action dismissal branch
    if not confirmation.is_confirmed:
        if current_status == "executing":
            raise HTTPException(
                status_code=409,
                detail="Cannot dismiss action confirmation while execution is currently in progress.",
            )
        if msg_record["is_confirmed"] and current_status in ("applied", "accepted"):
            raise HTTPException(
                status_code=409,
                detail="Cannot dismiss action that has already been confirmed and applied.",
            )

        meta["execution_status"] = "dismissed"
        await pool.execute(
            """
            UPDATE public.chat_messages
               SET is_confirmed = false,
                   metadata = $2::jsonb
             WHERE id = $1
            """,
            UUID(message_id),
            json.dumps(meta),
        )
        try:
            confirm_proposed_action(message_id, user_id, confirmation)
        except Exception as e:
            logger.warning("confirm_proposed_action sync update failed for %s: %s", message_id, e)

        updated_dict = _format_msg_dict(msg_record, map_id)
        updated_dict["is_confirmed"] = False
        updated_dict["metadata"] = meta

        return ActionConfirmResponse(
            message="Action confirmation dismissed.",
            is_confirmed=False,
            status="dismissed",
            data=None,
            updated_message=MessageResponse(**updated_dict),
        )

    # 2. Idempotent guard for already processed / in-flight confirmations
    if msg_record["is_confirmed"]:
        msg_resp = _format_msg_dict(msg_record, map_id)
        if current_status == "executing":
            return ActionConfirmResponse(
                message="Action confirmation is currently executing.",
                is_confirmed=True,
                status="executing",
                data=None,
                updated_message=MessageResponse(**msg_resp),
            )
        elif current_status == "accepted":
            return ActionConfirmResponse(
                message="Action has been accepted and queued for processing.",
                is_confirmed=True,
                status="accepted",
                data={"job_id": meta.get("job_id")},
                updated_message=MessageResponse(**msg_resp),
            )
        return ActionConfirmResponse(
            message="Action already confirmed and applied.",
            is_confirmed=True,
            status="applied",
            data=meta.get("execution_result"),
            updated_message=MessageResponse(**msg_resp),
        )

    # 3. Atomic CAS claiming execution state to prevent concurrent double-execution race
    execution_result = None
    if confirmation.is_confirmed and action_data:
        cas_claimed = await pool.fetchval(
            """
            UPDATE public.chat_messages
               SET is_confirmed = true,
                   metadata = (CASE WHEN jsonb_typeof(metadata) = 'object' THEN metadata ELSE '{}'::jsonb END) || '{"execution_status": "executing"}'::jsonb
             WHERE id = $1
               AND (is_confirmed IS NULL OR is_confirmed = false OR metadata->>'execution_status' IS NULL OR metadata->>'execution_status' IN ('pending', 'failed', 'dismissed'))
            RETURNING id
            """,
            UUID(message_id),
        )
        if not cas_claimed:
            reloaded = await pool.fetchrow(
                "SELECT id, role, content, proposed_action, is_confirmed, metadata, created_at FROM public.chat_messages WHERE id = $1",
                UUID(message_id),
            )
            reloaded_meta = _safe_parse_json_dict(reloaded.get("metadata")) if reloaded else {}
            reloaded_status = reloaded_meta.get("execution_status")

            if reloaded_status == "executing":
                return ActionConfirmResponse(
                    message="Action confirmation is currently executing.",
                    is_confirmed=True,
                    status="executing",
                    data=None,
                    updated_message=MessageResponse(**_format_msg_dict(reloaded, map_id)) if reloaded else None,
                )
            elif reloaded_status == "accepted":
                return ActionConfirmResponse(
                    message="Action has been accepted and queued for processing.",
                    is_confirmed=True,
                    status="accepted",
                    data={"job_id": reloaded_meta.get("job_id")},
                    updated_message=MessageResponse(**_format_msg_dict(reloaded, map_id)) if reloaded else None,
                )
            return ActionConfirmResponse(
                message="Action already confirmed and applied.",
                is_confirmed=True,
                status="applied",
                data=reloaded_meta.get("execution_result"),
                updated_message=MessageResponse(**_format_msg_dict(reloaded, map_id)) if reloaded else None,
            )

        action_type = (
            action_data.get("action_type") or action_data.get("action") or action_data.get("intent") or ""
        ).lower()
        instruction = (
            action_data.get("instruction")
            or action_data.get("explanation")
            or action_data.get("description")
            or "Execute proposed change"
        )

        try:
            if action_type in ("regen_whole", "regenerate", "regenerate_full"):
                execution_result = await mindmap_service.regenerate_full(
                    map_id=map_id,
                    user_id=user_uuid,
                    instruction=instruction,
                )
            elif action_type in ("regen_node", "regenerate_node", "regenerate_branch"):
                raw_target = action_data.get("target_node_id") or (action_data.get("target_node_ids") or [None])[0]
                if not raw_target:
                    raise HTTPException(status_code=400, detail="Target node ID is required for node regeneration.")
                target_nid = UUID(str(raw_target))
                execution_result = await mindmap_service.regenerate_node(
                    map_id=map_id,
                    node_id=target_nid,
                    user_id=user_uuid,
                    instruction=instruction,
                )
            elif action_type in ("edit_proposal", "graph_edit"):
                params = action_data.get("parameters") or {}
                raw_nodes = params.get("nodes") or []
                raw_edges = params.get("edges") or []
                update_nodes = list(params.get("update_nodes") or [])
                create_nodes = list(params.get("create_nodes") or [])
                delete_nodes = list(params.get("delete_nodes") or [])
                update_edges = list(params.get("update_edges") or [])
                create_edges = list(params.get("create_edges") or [])
                delete_edges = list(params.get("delete_edges") or [])

                # Verify proposal has non-empty parameters
                if (
                    not raw_nodes
                    and not raw_edges
                    and not update_nodes
                    and not create_nodes
                    and not delete_nodes
                    and not update_edges
                    and not create_edges
                    and not delete_edges
                ):
                    raise HTTPException(status_code=400, detail="Edit proposal contains no modifications.")

                # If generic 'nodes' or 'edges' provided, categorize into delta updates vs creations
                # to prevent wholesale branch deletion of unmentioned nodes (N1)
                existing_node_rows = await pool.fetch("SELECT id FROM public.nodes WHERE map_id = $1", map_id)
                existing_node_ids = {str(r["id"]) for r in existing_node_rows}
                for n in raw_nodes:
                    nid = str(n.get("id")) if isinstance(n, dict) else str(getattr(n, "id", ""))
                    if nid and nid in existing_node_ids:
                        update_nodes.append(n)
                    else:
                        create_nodes.append(n)

                existing_edge_rows = await pool.fetch("SELECT id FROM public.edges WHERE map_id = $1", map_id)
                existing_edge_ids = {str(r["id"]) for r in existing_edge_rows}
                for edge_item in raw_edges:
                    eid = str(edge_item.get("id")) if isinstance(edge_item, dict) else str(getattr(edge_item, "id", ""))
                    if eid and eid in existing_edge_ids:
                        update_edges.append(edge_item)
                    else:
                        create_edges.append(edge_item)

                # Take safety snapshot before applying delta edit
                from snapshots.service import create_map_snapshot

                await create_map_snapshot(
                    pool,
                    map_id=map_id,
                    reason=f"Pre-edit snapshot before applying AI edit proposal ({message_id})",
                )

                # Dispatch delta updates ONLY (never wholesale reconciliation)
                delta_payload: dict[str, Any] = {}
                if update_nodes:
                    delta_payload["update_nodes"] = update_nodes
                if create_nodes:
                    delta_payload["create_nodes"] = create_nodes
                if delete_nodes:
                    root_rows = await pool.fetch(
                        "SELECT id FROM public.nodes WHERE map_id = $1 AND node_type = 'root'", map_id
                    )
                    root_ids = {str(r["id"]) for r in root_rows}
                    for item in delete_nodes:
                        nid = str(item.get("id") if isinstance(item, dict) else item)
                        if nid in root_ids:
                            raise HTTPException(
                                status_code=400,
                                detail="Cannot delete central root node of the mind map via action proposal",
                            )
                    delta_payload["delete_nodes"] = delete_nodes
                if update_edges:
                    delta_payload["update_edges"] = update_edges
                if create_edges:
                    delta_payload["create_edges"] = create_edges
                if delete_edges:
                    delta_payload["delete_edges"] = delete_edges

                execution_result = await mindmap_service.batch_update_graph(
                    map_id=map_id,
                    user_id=user_uuid,
                    updates=delta_payload,
                )
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported action type: {action_type}")
        except HTTPException:
            await pool.execute(
                """
                UPDATE public.chat_messages
                   SET is_confirmed = false,
                       metadata = (CASE WHEN jsonb_typeof(metadata) = 'object' THEN metadata ELSE '{}'::jsonb END) || '{"execution_status": "failed"}'::jsonb
                 WHERE id = $1
                """,
                UUID(message_id),
            )
            raise
        except Exception as exc:
            await pool.execute(
                """
                UPDATE public.chat_messages
                   SET is_confirmed = false,
                       metadata = (CASE WHEN jsonb_typeof(metadata) = 'object' THEN metadata ELSE '{}'::jsonb END) || '{"execution_status": "failed"}'::jsonb
                 WHERE id = $1
                """,
                UUID(message_id),
            )
            raise HTTPException(status_code=500, detail=f"Action execution failed: {exc}")

        # Persist execution status and outcome into message metadata
        final_meta = _safe_parse_json_dict(msg_record.get("metadata"))
        status_val = "applied"
        if isinstance(execution_result, dict) and execution_result.get("job_id"):
            status_val = "accepted"
            final_meta["job_id"] = str(execution_result["job_id"])
        final_meta["execution_status"] = status_val
        if execution_result:
            final_meta["execution_result"] = execution_result
        try:
            await pool.execute(
                "UPDATE public.chat_messages SET metadata = $2::jsonb WHERE id = $1",
                UUID(message_id),
                json.dumps(final_meta),
            )
        except Exception as meta_exc:
            logger.warning("Failed to persist execution metadata for message %s: %s", message_id, meta_exc)

    try:
        confirm_proposed_action(message_id, user_id, confirmation)
    except Exception as e:
        logger.warning("confirm_proposed_action sync update failed for %s: %s", message_id, e)

    updated_dict = _format_msg_dict(msg_record, map_id)
    updated_dict["is_confirmed"] = True
    updated_dict["metadata"] = final_meta

    msg_text = "Action confirmed and applied."
    if status_val == "accepted":
        msg_text = "Action confirmed and queued for processing."

    return ActionConfirmResponse(
        message=msg_text,
        is_confirmed=True,
        status=status_val,
        data=execution_result,
        updated_message=MessageResponse(**updated_dict),
    )
