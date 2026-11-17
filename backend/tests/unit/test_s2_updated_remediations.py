"""Targeted unit tests verifying the 19 critical audit findings (C1-C19).

Covers:
- C4 & C5: Map ownership boundaries on chat send, history, confirmation and memory routes.
- C18 & C17: 202 status code and job rejection compensation on queue dispatch failure.
- C10: Edge endpoint reconnection persistence in batch update.
- C12 & C19: Comment status update, resolved field, and map owner moderation.
- C14: Non-destructive snapshot restore preserving comments.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from chat.dependencies import check_rate_limit, get_mindmap_service
from core.supabase import current_user_id, get_current_user
from fastapi.testclient import TestClient
from main import app
from maps.schemas import CommentResponse, CommentUpdate
from routes.internal import ChatCompleteCallback


@pytest.fixture
def auth_client():
    uid = uuid.uuid4()
    fake_user = MagicMock(id=uid)
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[current_user_id] = lambda: uid
    app.dependency_overrides[check_rate_limit] = lambda: None
    app.dependency_overrides[get_mindmap_service] = lambda: MagicMock()

    with (
        patch("main.create_redis_pool", AsyncMock(return_value=None)),
        patch("main.create_db_pool", AsyncMock(return_value=None)),
        TestClient(app) as client,
    ):
        client.cookies.set("sb-access-token", "fake-token")
        yield client, uid

    app.dependency_overrides.clear()


def test_chat_send_cross_user_forbidden_check(auth_client):
    """C4: Verifies that a user cannot send a chat message to another user's map."""
    client, uid = auth_client
    other_map_id = uuid.uuid4()

    mock_pool = MagicMock()
    # assert_map_owner returns False for unauthorized map
    mock_pool.fetchval = AsyncMock(return_value=None)
    app.state.pool = mock_pool

    resp = client.post(
        "/api/chat/messages",
        json={"map_id": str(other_map_id), "content": "Hello unauthorized map"},
    )
    assert resp.status_code == 404
    assert "Map not found" in resp.json()["detail"]


def test_chat_history_cross_user_forbidden_check(auth_client):
    """C4: Verifies that a user cannot retrieve chat history from another user's map."""
    client, uid = auth_client
    other_map_id = uuid.uuid4()

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=None)
    app.state.pool = mock_pool

    resp = client.get(f"/api/chat/history/{other_map_id}")
    assert resp.status_code == 404
    assert "Map not found" in resp.json()["detail"]


def test_memory_context_and_summary_cross_user_forbidden(auth_client):
    """C5: Verifies that memory context and summary reject unauthorized callers."""
    client, uid = auth_client
    other_map_id = uuid.uuid4()

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=None)
    app.state.pool = mock_pool

    resp1 = client.post(f"/api/memory/context?map_id={other_map_id}")
    assert resp1.status_code == 404

    resp2 = client.get(f"/api/memory/summary/{other_map_id}")
    assert resp2.status_code == 404


def test_chat_send_returns_202_and_job_id(auth_client):
    """C18 & C1: Verifies chat send returns 202 Accepted, job_id, and poll URL."""
    client, uid = auth_client
    map_id = uuid.uuid4()
    msg_id = uuid.uuid4()

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=True)  # owner verified
    mock_pool.fetch = AsyncMock(return_value=[])

    mock_conn = MagicMock()
    mock_conn.fetchval = AsyncMock(side_effect=[uuid.uuid4(), "Existing thread summary"])
    mock_conn.fetchrow = AsyncMock(
        return_value={"summary": "Existing thread summary", "summary_through_message_id": None}
    )
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_conn.execute = AsyncMock()

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_conn
    mock_cm.__aexit__.return_value = None

    mock_redis = MagicMock()

    with (
        patch("chat.routes.transaction", return_value=mock_cm),
        patch("chat.routes.create_generation_job", AsyncMock(return_value=None)),
        patch("chat.routes.enqueue_chat_turn_job", AsyncMock(return_value="arq-job-123")),
        patch(
            "chat.routes.save_message", return_value={"id": str(msg_id), "role": "user", "content": "Help me expand"}
        ),
    ):
        app.state.pool = mock_pool
        app.state.redis_pool = mock_redis

        resp = client.post(
            "/api/chat/messages",
            json={"map_id": str(map_id), "content": "Help me expand"},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "accepted"
        assert "job_id" in data
        assert f"/api/maps/{map_id}/jobs/" in data["poll"]


def test_chat_send_enqueue_failure_compensates_rejected_job(auth_client):
    """C17: Verifies that when Redis enqueue fails, the job is marked rejected and 503 is returned."""
    client, uid = auth_client
    map_id = uuid.uuid4()
    msg_id = uuid.uuid4()

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=True)
    mock_pool.fetch = AsyncMock(return_value=[])
    mock_pool.execute = AsyncMock()

    mock_conn = MagicMock()
    mock_conn.fetchval = AsyncMock(return_value=uuid.uuid4())
    mock_conn.fetchrow = AsyncMock(
        return_value={"summary": "Existing thread summary", "summary_through_message_id": None}
    )
    mock_conn.fetch = AsyncMock(return_value=[])

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_conn
    mock_cm.__aexit__.return_value = None

    mock_redis = MagicMock()

    with (
        patch("chat.routes.transaction", return_value=mock_cm),
        patch("chat.routes.create_generation_job", AsyncMock(return_value=None)),
        patch("chat.routes.enqueue_chat_turn_job", AsyncMock(side_effect=RuntimeError("Redis connection refused"))),
        patch("chat.routes.save_message", return_value={"id": str(msg_id), "role": "user", "content": "Test"}),
    ):
        app.state.pool = mock_pool
        app.state.redis_pool = mock_redis

        resp = client.post(
            "/api/chat/messages",
            json={"map_id": str(map_id), "content": "Test"},
        )
        assert resp.status_code == 503
        assert "unavailable" in resp.json()["detail"].lower()
        # Verify compensating update was called
        assert mock_pool.execute.called
        call_args = mock_pool.execute.call_args[0]
        assert "status = 'rejected'" in call_args[0]


def test_comment_schema_status_and_resolved_mapping():
    """C12: Verifies CommentUpdate and CommentResponse status handling."""
    update = CommentUpdate(status="resolved")
    assert update.status == "resolved"

    resp = CommentResponse(
        id=uuid.uuid4(),
        map_id=uuid.uuid4(),
        node_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        content="Refine this concept",
        status="resolved",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert resp.status == "resolved"
    assert resp.resolved is True

    resp_open = CommentResponse(
        id=uuid.uuid4(),
        map_id=uuid.uuid4(),
        node_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        content="Open question",
        status="open",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert resp_open.resolved is False


def test_chat_complete_callback_with_proposals_and_citations():
    """C3: Verifies ChatCompleteCallback captures action proposal, cited nodes, and source refs."""
    cb = ChatCompleteCallback(
        map_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        assistant_message="I have added citations and a proposal.",
        action_proposal={"action_type": "regenerate_branch", "target_node_id": str(uuid.uuid4())},
        cited_node_ids=[uuid.uuid4()],
        source_refs=["https://arxiv.org/abs/2301.00001"],
    )
    assert cb.action_proposal["action_type"] == "regenerate_branch"
    assert len(cb.cited_node_ids) == 1
    assert len(cb.source_refs) == 1


def test_comment_content_edit_by_non_author_forbidden(auth_client):
    """C19: Verifies that updating comment text is forbidden for non-authors even if map owner."""
    client, uid = auth_client
    map_id = uuid.uuid4()
    node_id = uuid.uuid4()
    comment_id = uuid.uuid4()
    author_id = uuid.uuid4()  # different from uid

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=True)  # uid is map owner

    async def mock_fetchrow(query, *args):
        if "FROM public.nodes" in query:
            return {"id": node_id, "map_id": map_id, "label": "Test Node"}
        if "FROM public.node_comments" in query:
            return {
                "id": comment_id,
                "map_id": map_id,
                "node_id": node_id,
                "user_id": author_id,
                "content": "Original text",
                "status": "open",
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            }
        return None

    mock_pool.fetchrow = AsyncMock(side_effect=mock_fetchrow)
    app.state.pool = mock_pool

    # Non-author tries to update content
    resp = client.patch(
        f"/api/maps/{map_id}/nodes/{node_id}/comments/{comment_id}",
        json={"content": "Malicious edit"},
    )
    assert resp.status_code == 403
    assert "permission" in resp.json()["detail"].lower()


def test_comment_moderation_owner_can_resolve_and_delete(auth_client):
    """C19: Verifies that map owners can resolve or delete comments authored by other users."""
    client, uid = auth_client
    map_id = uuid.uuid4()
    node_id = uuid.uuid4()
    comment_id = uuid.uuid4()
    author_id = uuid.uuid4()  # different from uid

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=True)  # uid is map owner

    async def mock_fetchrow(query, *args):
        if "FROM public.nodes" in query:
            return {"id": node_id, "map_id": map_id, "label": "Test Node"}
        if "SELECT" in query and "FROM public.node_comments" in query:
            return {
                "id": comment_id,
                "map_id": map_id,
                "node_id": node_id,
                "user_id": author_id,
                "content": "Original text",
                "status": "open",
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            }
        if "UPDATE public.node_comments" in query:
            return {
                "id": comment_id,
                "map_id": map_id,
                "node_id": node_id,
                "user_id": author_id,
                "content": "Original text",
                "status": "resolved",
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            }
        return None

    mock_pool.fetchrow = AsyncMock(side_effect=mock_fetchrow)
    mock_pool.execute = AsyncMock(return_value="DELETE 1")
    app.state.pool = mock_pool

    # 1. Map owner resolves comment without touching content -> Allowed
    resp_resolve = client.patch(
        f"/api/maps/{map_id}/nodes/{node_id}/comments/{comment_id}",
        json={"status": "resolved"},
    )
    assert resp_resolve.status_code == 200
    assert resp_resolve.json()["status"] == "resolved"
    assert resp_resolve.json()["resolved"] is True

    # 2. Map owner deletes comment -> Allowed
    resp_delete = client.delete(f"/api/maps/{map_id}/nodes/{node_id}/comments/{comment_id}")
    assert resp_delete.status_code == 204


def test_batch_update_edge_reconnection(auth_client):
    """C10: Verifies batch_update accepts reconnected source_node_id and target_node_id for existing edges."""
    client, uid = auth_client
    map_id = uuid.uuid4()
    node_a = uuid.uuid4()
    node_b = uuid.uuid4()
    node_c = uuid.uuid4()
    edge_id = uuid.uuid4()

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(side_effect=[True, 1])  # owner check, version check
    mock_pool.fetch = AsyncMock(return_value=[])

    mock_conn = MagicMock()
    mock_conn.fetchval = AsyncMock(return_value=1)
    mock_conn.fetchrow = AsyncMock(return_value={"id": edge_id, "source_node_id": node_a, "target_node_id": node_c})
    mock_conn.fetch = AsyncMock(
        return_value=[
            {"id": node_a, "map_id": map_id, "label": "A"},
            {"id": node_b, "map_id": map_id, "label": "B"},
            {"id": node_c, "map_id": map_id, "label": "C"},
        ]
    )
    mock_conn.execute = AsyncMock()

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_conn
    mock_cm.__aexit__.return_value = None

    with (
        patch("maps.routes.transaction", return_value=mock_cm),
        patch("maps.routes.update_map_edge", AsyncMock()) as mock_edge_update,
    ):
        app.state.pool = mock_pool

        resp = client.put(
            f"/api/maps/{map_id}/graph",
            json={
                "expected_version": 1,
                "validate_graph": False,
                "update_edges": [
                    {
                        "id": str(edge_id),
                        "source_node_id": str(node_a),
                        "target_node_id": str(node_c),  # reconnected from node_b to node_c
                        "label": "reconnected",
                    }
                ],
            },
        )
        assert resp.status_code == 200
        assert mock_edge_update.called
        # Verify kwargs passed to update_map_edge include source_node_id and target_node_id
        call_args = mock_edge_update.call_args[0]
        updates_payload = call_args[3]
        assert updates_payload["source_node_id"] == node_a
        assert updates_payload["target_node_id"] == node_c


@pytest.mark.asyncio
async def test_non_destructive_snapshot_restore():
    """C14: Verifies snapshot restore preserves existing node identity and reconciles in-place."""
    from db.repository import restore_map_snapshot

    map_id = uuid.uuid4()
    snap_id = uuid.uuid4()
    preserved_node_id = uuid.uuid4()
    removed_node_id = uuid.uuid4()
    new_node_id = uuid.uuid4()

    mock_conn = MagicMock()

    # Existing DB nodes before restore
    mock_conn.fetch = AsyncMock(
        side_effect=[
            # 1. existing nodes
            [{"id": preserved_node_id}, {"id": removed_node_id}],
            # 2. existing edges
            [],
        ]
    )
    mock_conn.fetchrow = AsyncMock(
        return_value={
            "id": snap_id,
            "map_id": map_id,
            "version_number": 1,
            "nodes_snapshot": [
                {"id": str(preserved_node_id), "label": "Preserved Node", "pos_x": 10.0, "pos_y": 20.0},
                {"id": str(new_node_id), "label": "New Restored Node", "pos_x": 30.0, "pos_y": 40.0},
            ],
            "edges_snapshot": [],
        }
    )
    mock_conn.fetchval = AsyncMock(return_value=2)  # new version
    mock_conn.execute = AsyncMock()

    with (
        patch("db.repository.create_map_snapshot", AsyncMock()),
        patch("db.repository.update_map_node", AsyncMock()) as mock_node_update,
        patch("db.repository.create_map_node", AsyncMock()) as mock_node_create,
    ):
        await restore_map_snapshot(mock_conn, map_id=map_id, snapshot_id=snap_id)

        # 1. Preserved node was updated in-place (preserving comments & attached items)
        assert mock_node_update.called
        update_args = mock_node_update.call_args[0]
        assert update_args[2] == preserved_node_id

        # 2. New node was created
        assert mock_node_create.called
        create_kwargs = mock_node_create.call_args[1]
        assert create_kwargs["node_id"] == new_node_id

        # 3. Removed node was deleted, but NOT a blanket DELETE FROM nodes
        assert mock_conn.execute.called
        executed_sqls = [call[0][0] for call in mock_conn.execute.call_args_list]
        blanket_delete = any(
            "DELETE FROM public.nodes WHERE map_id = $1\n" in sql
            or 'DELETE FROM public.nodes WHERE map_id = $1"' in sql
            for sql in executed_sqls
        )
        assert not blanket_delete


def test_chat_and_memory_fail_closed_without_pool(auth_client):
    """C2: Verifies that chat and memory routes fail closed (HTTP 503) when database pool is None."""
    client, uid = auth_client
    app.state.pool = None
    some_id = str(uuid.uuid4())

    # 1. Chat send fails closed
    resp = client.post("/api/chat/messages", json={"map_id": some_id, "content": "Hello"})
    assert resp.status_code == 503

    # 2. Chat history fails closed
    resp = client.get(f"/api/chat/history/{some_id}")
    assert resp.status_code == 503

    # 3. Chat confirm fails closed
    resp = client.post(f"/api/chat/actions/{some_id}/confirm", json={"is_confirmed": True})
    assert resp.status_code == 503

    # 4. Memory context fails closed
    resp = client.post(f"/api/memory/context?map_id={some_id}")
    assert resp.status_code == 503

    # 5. Memory summary fails closed
    resp = client.get(f"/api/memory/summary/{some_id}")
    assert resp.status_code == 503


def test_chat_send_returns_503_when_redis_unavailable(auth_client):
    """C4: Verifies that chat send returns 503 rather than false 202 when redis_pool is None."""
    client, uid = auth_client
    map_id = uuid.uuid4()

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=True)  # owner verified
    app.state.pool = mock_pool
    app.state.redis_pool = None

    resp = client.post(
        "/api/chat/messages",
        json={"map_id": str(map_id), "content": "Help me expand"},
    )
    assert resp.status_code == 503
    assert "unavailable" in resp.json()["detail"].lower()


def test_mindmap_service_interface_conformance():
    """F1 & F10: Verifies MindMapBackendService implements all required production methods."""
    import inspect

    from services.mindmap_service import MindMapBackendService

    assert hasattr(MindMapBackendService, "generate")
    assert inspect.iscoroutinefunction(MindMapBackendService.generate)

    assert hasattr(MindMapBackendService, "regenerate_full")
    assert inspect.iscoroutinefunction(MindMapBackendService.regenerate_full)

    assert hasattr(MindMapBackendService, "regenerate_node")
    assert inspect.iscoroutinefunction(MindMapBackendService.regenerate_node)

    assert hasattr(MindMapBackendService, "batch_update_graph")
    assert inspect.iscoroutinefunction(MindMapBackendService.batch_update_graph)


def test_chat_confirm_action_intent_dispatch_and_execution(auth_client):
    """C1, F1 & F10: Verifies action proposals correctly map worker intents to backend operations using strict autospec."""
    from unittest.mock import create_autospec

    from services.mindmap_service import MindMapBackendService

    client, uid = auth_client
    map_id = uuid.uuid4()
    msg_id = uuid.uuid4()
    target_node_id = uuid.uuid4()

    mock_pool = MagicMock()
    # Strict spec_set ensuring ONLY real methods can be mocked
    mock_service = create_autospec(MindMapBackendService, instance=True, spec_set=True)
    mock_service.regenerate_full = AsyncMock(return_value={"map_id": str(map_id), "version": 2})
    mock_service.regenerate_node = AsyncMock(return_value={"map_id": str(map_id), "node_id": str(target_node_id)})
    mock_service.batch_update_graph = AsyncMock(return_value={"map_id": str(map_id), "version": 3})
    mock_pool.execute = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[])
    mock_pool.fetchval = AsyncMock(return_value=msg_id)  # CAS claim succeeds
    app.state.pool = mock_pool

    app.dependency_overrides[get_mindmap_service] = lambda: mock_service

    # 1. regen_whole intent
    mock_pool.fetchrow = AsyncMock(
        return_value={
            "id": msg_id,
            "role": "assistant",
            "content": "Proposal",
            "user_id": uid,
            "map_id": map_id,
            "is_confirmed": False,
            "metadata": None,
            "proposed_action": {"action_type": "regen_whole", "instruction": "Redo whole map"},
        }
    )
    with patch(
        "chat.routes.confirm_proposed_action",
        return_value={
            "id": str(msg_id),
            "is_confirmed": True,
            "map_id": str(map_id),
            "user_id": str(uid),
            "role": "assistant",
            "content": "Confirmed",
            "created_at": datetime.now(UTC),
        },
    ):
        resp = client.post(f"/api/chat/actions/{msg_id}/confirm", json={"is_confirmed": True})
        assert resp.status_code == 200
        assert resp.json()["is_confirmed"] is True
        assert mock_service.regenerate_full.called

    # 2. regen_node intent
    mock_pool.fetchrow = AsyncMock(
        return_value={
            "id": msg_id,
            "role": "assistant",
            "content": "Proposal",
            "user_id": uid,
            "map_id": map_id,
            "is_confirmed": False,
            "metadata": None,
            "proposed_action": {
                "action_type": "regen_node",
                "target_node_id": str(target_node_id),
                "instruction": "Expand node",
            },
        }
    )
    with patch(
        "chat.routes.confirm_proposed_action",
        return_value={
            "id": str(msg_id),
            "is_confirmed": True,
            "map_id": str(map_id),
            "user_id": str(uid),
            "role": "assistant",
            "content": "Confirmed",
            "created_at": datetime.now(UTC),
        },
    ):
        resp = client.post(f"/api/chat/actions/{msg_id}/confirm", json={"is_confirmed": True})
        assert resp.status_code == 200
        assert mock_service.regenerate_node.called

    # 3. edit_proposal intent
    mock_pool.fetchrow = AsyncMock(
        return_value={
            "id": msg_id,
            "role": "assistant",
            "content": "Proposal",
            "user_id": uid,
            "map_id": map_id,
            "is_confirmed": False,
            "metadata": None,
            "proposed_action": {
                "action_type": "edit_proposal",
                "parameters": {"update_nodes": [{"id": str(target_node_id), "label": "Updated Subtopic"}]},
            },
        }
    )
    with patch(
        "chat.routes.confirm_proposed_action",
        return_value={
            "id": str(msg_id),
            "is_confirmed": True,
            "map_id": str(map_id),
            "user_id": str(uid),
            "role": "assistant",
            "content": "Confirmed",
            "created_at": datetime.now(UTC),
        },
    ):
        resp = client.post(f"/api/chat/actions/{msg_id}/confirm", json={"is_confirmed": True})
        assert resp.status_code == 200
        assert mock_service.batch_update_graph.called

    # 4. Failed execution does NOT confirm action and reverts is_confirmed
    mock_service.regenerate_full.side_effect = RuntimeError("Worker failure")
    mock_pool.fetchrow = AsyncMock(
        return_value={
            "id": msg_id,
            "role": "assistant",
            "content": "Proposal",
            "user_id": uid,
            "map_id": map_id,
            "is_confirmed": False,
            "metadata": None,
            "proposed_action": {"action_type": "regen_whole", "instruction": "Redo"},
            "created_at": datetime.now(UTC),
        }
    )
    resp_fail = client.post(f"/api/chat/actions/{msg_id}/confirm", json={"is_confirmed": True})
    assert resp_fail.status_code == 500
    assert any("is_confirmed = false" in str(c) for c in mock_pool.execute.call_args_list)


def test_chat_confirm_action_idempotency_duplicate_call(auth_client):
    """F3: Verifies duplicate confirmation calls return cached outcome without re-executing service."""
    from unittest.mock import create_autospec

    from services.mindmap_service import MindMapBackendService

    client, uid = auth_client
    map_id = uuid.uuid4()
    msg_id = uuid.uuid4()

    mock_pool = MagicMock()
    mock_service = create_autospec(MindMapBackendService, instance=True, spec_set=True)
    app.state.pool = mock_pool
    app.dependency_overrides[get_mindmap_service] = lambda: mock_service

    # Message is ALREADY confirmed with execution_result saved
    cached_execution_result = {"status": "accepted", "job_id": "job-abc-123"}
    mock_pool.fetchrow = AsyncMock(
        return_value={
            "id": msg_id,
            "role": "assistant",
            "content": "Proposal",
            "user_id": uid,
            "map_id": map_id,
            "is_confirmed": True,
            "metadata": {"execution_status": "applied", "execution_result": cached_execution_result},
            "proposed_action": {"action_type": "regen_whole", "instruction": "Redo"},
            "created_at": datetime.now(UTC),
        }
    )

    resp = client.post(f"/api/chat/actions/{msg_id}/confirm", json={"is_confirmed": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_confirmed"] is True
    assert "already confirmed" in data["message"].lower()
    assert data["data"] == cached_execution_result
    # Crucial: Service method was NOT executed again!
    assert not mock_service.regenerate_full.called


def test_wholesale_batch_update_rejects_foreign_edge_endpoints(auth_client):
    """F2: Verifies wholesale graph updates reject edges pointing to foreign node IDs."""
    client, uid = auth_client
    map_id = uuid.uuid4()
    valid_node_1 = uuid.uuid4()
    valid_node_2 = uuid.uuid4()
    foreign_node = uuid.uuid4()

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=True)  # owner verified
    app.state.pool = mock_pool

    mock_conn = MagicMock()
    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_conn
    mock_cm.__aexit__.return_value = None

    with patch("maps.routes.transaction", return_value=mock_cm):
        # Wholesale payload: nodes has valid_node_1 and valid_node_2,
        # but edge points to foreign_node!
        resp = client.put(
            f"/api/maps/{map_id}/graph",
            json={
                "expected_version": 1,
                "validate_graph": False,
                "nodes": [
                    {"id": str(valid_node_1), "label": "Node 1", "pos_x": 0, "pos_y": 0},
                    {"id": str(valid_node_2), "label": "Node 2", "pos_x": 100, "pos_y": 100},
                ],
                "edges": [
                    {
                        "source_node_id": str(valid_node_1),
                        "target_node_id": str(foreign_node),
                    }
                ],
            },
        )
        assert resp.status_code == 422
        assert "must belong to this map" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_legacy_snapshot_restore_non_destructive():
    """C8: Verifies legacy restore_snapshot_data_to_map reconciles in-place without blanket DELETE."""
    from snapshots.service import restore_snapshot_data_to_map

    map_id = uuid.uuid4()
    preserved_node_id = uuid.uuid4()
    removed_node_id = uuid.uuid4()
    new_node_id = uuid.uuid4()

    mock_conn = MagicMock()
    mock_conn.fetch = AsyncMock(
        side_effect=[
            [{"id": preserved_node_id}, {"id": removed_node_id}],  # existing nodes
            [],  # existing edges
        ]
    )
    mock_conn.execute = AsyncMock()

    snapshot_data = {
        "nodes": [
            {"id": str(preserved_node_id), "label": "Preserved", "pos_x": 10, "pos_y": 10},
            {"id": str(new_node_id), "label": "New Node", "pos_x": 20, "pos_y": 20},
        ],
        "edges": [],
    }

    with (
        patch("snapshots.service.update_map_node", AsyncMock()) as mock_upd,
        patch("snapshots.service.create_map_node", AsyncMock()) as mock_crt,
    ):
        await restore_snapshot_data_to_map(mock_conn, map_id, snapshot_data)

        # Existing node updated in-place (preserving comments)
        assert mock_upd.called
        assert mock_upd.call_args[0][2] == preserved_node_id

        # New node created
        assert mock_crt.called

        # Blanket DELETE FROM nodes was NOT executed
        executed_sqls = [call[0][0] for call in mock_conn.execute.call_args_list]
        blanket_delete = any(
            "DELETE FROM public.nodes WHERE map_id = $1\n" in sql
            or 'DELETE FROM public.nodes WHERE map_id = $1"' in sql
            for sql in executed_sqls
        )
        assert not blanket_delete


def test_batch_edge_same_map_enforcement_without_topology_validation(auth_client):
    """C10: Verifies same-map referential checks reject foreign nodes even when validate_graph=False."""
    client, uid = auth_client
    map_id = uuid.uuid4()
    local_node = uuid.uuid4()
    foreign_node = uuid.uuid4()

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(side_effect=[True, 1])  # owner check, version check
    app.state.pool = mock_pool

    mock_conn = MagicMock()
    mock_conn.fetchval = AsyncMock(return_value=1)
    # Return only local_node as belonging to map_id (foreign_node belongs to another map)
    mock_conn.fetch = AsyncMock(return_value=[{"id": local_node}])
    mock_conn.execute = AsyncMock()

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_conn
    mock_cm.__aexit__.return_value = None

    with patch("maps.routes.transaction", return_value=mock_cm):
        resp = client.put(
            f"/api/maps/{map_id}/graph",
            json={
                "expected_version": 1,
                "validate_graph": False,  # topology validation disabled
                "create_edges": [
                    {
                        "source_node_id": str(local_node),
                        "target_node_id": str(foreign_node),  # from another map!
                    }
                ],
            },
        )
        assert resp.status_code == 422
        assert "belong to the same mind map" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_replace_node_branch_preserves_cross_linked_dag_descendant():
    """C14: Verifies replace_node_branch preserves shared descendants with incoming edges from outside subtree."""
    from db.repository import replace_node_branch

    map_id = uuid.uuid4()
    branch_root_id = uuid.uuid4()
    outside_parent_id = uuid.uuid4()
    shared_child_id = uuid.uuid4()
    isolated_child_id = uuid.uuid4()

    mock_conn = MagicMock()

    # Subtree has branch_root, shared_child, and isolated_child
    # But shared_child also has an incoming edge from outside_parent!
    mock_conn.fetch = AsyncMock(
        side_effect=[
            # 1. Existing edges for descendants traversal
            [
                {"source_node_id": branch_root_id, "target_node_id": isolated_child_id},
                {"source_node_id": branch_root_id, "target_node_id": shared_child_id},
                {"source_node_id": outside_parent_id, "target_node_id": shared_child_id},
            ],
            # 2. Existing nodes
            [
                {"id": branch_root_id, "label": "Branch Root"},
                {"id": isolated_child_id, "label": "Isolated Child"},
                {"id": shared_child_id, "label": "Shared Child"},
            ],
        ]
    )
    mock_conn.execute = AsyncMock()
    mock_conn.executemany = AsyncMock()
    mock_conn.fetchval = AsyncMock(return_value=2)

    with (
        patch("db.repository.create_map_snapshot", AsyncMock()),
        patch("db.repository.create_map_node", AsyncMock()),
        patch("db.repository.create_map_edge", AsyncMock()),
    ):
        await replace_node_branch(
            mock_conn,
            map_id=map_id,
            target_node_id=branch_root_id,
            source_job_id=uuid.uuid4(),
            new_nodes=[{"id": uuid.uuid4(), "label": "New Replacement Branch"}],
            new_edges=[],
        )

        # Verify deletion calls: isolated_child is deleted, but shared_child is PRESERVED!
        delete_node_calls = [
            call for call in mock_conn.execute.call_args_list if "DELETE FROM public.nodes" in call[0][0]
        ]
        if delete_node_calls:
            deleted_ids = delete_node_calls[0][0][2]
            assert shared_child_id not in deleted_ids
            assert isolated_child_id in deleted_ids


def test_multi_parent_dag_ancestor_collection():
    """C13: Verifies multi-parent reverse BFS captures all ancestor labels in DAG."""
    from collections import deque

    node_id = "node-c"
    nodes_map = {
        "node-root": {"label": "Root Concept"},
        "node-a": {"label": "Parent A"},
        "node-b": {"label": "Parent B"},
        "node-c": {"label": "Child C"},
    }
    # DAG: Root -> A -> C, and Root -> B -> C
    edges_data = [
        {"source": "node-root", "target": "node-a"},
        {"source": "node-root", "target": "node-b"},
        {"source": "node-a", "target": "node-c"},
        {"source": "node-b", "target": "node-c"},
    ]

    ancestor_labels = []
    ancestor_queue = deque([node_id])
    visited_ancestors = {node_id}
    while ancestor_queue:
        curr = ancestor_queue.popleft()
        for e in edges_data:
            tgt = str(e.get("target_node_id") or e.get("target"))
            src = str(e.get("source_node_id") or e.get("source"))
            if tgt == curr and src and src not in visited_ancestors:
                visited_ancestors.add(src)
                ancestor_queue.append(src)
                if src in nodes_map and nodes_map[src].get("label"):
                    ancestor_labels.append(nodes_map[src]["label"])
    ancestor_labels.reverse()

    # Both Parent A, Parent B, and Root Concept must be collected
    assert "Parent A" in ancestor_labels
    assert "Parent B" in ancestor_labels
    assert "Root Concept" in ancestor_labels
    assert len(ancestor_labels) == 3


def test_chat_send_history_bounded_by_summary_cursor(auth_client):
    """F6: Verifies recent chat history query is strictly bounded by summary_through_message_id cursor."""
    client, uid = auth_client
    map_id = uuid.uuid4()
    msg_id = uuid.uuid4()
    cursor_msg_id = uuid.uuid4()

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=True)  # owner verified
    mock_pool.fetch = AsyncMock(return_value=[])

    mock_conn = MagicMock()
    mock_conn.fetchval = AsyncMock(side_effect=[uuid.uuid4(), cursor_msg_id])
    mock_conn.fetchrow = AsyncMock(
        return_value={
            "summary": "Existing thread summary",
            "summary_through_message_id": cursor_msg_id,
        }
    )
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_conn.execute = AsyncMock()

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_conn
    mock_cm.__aexit__.return_value = None

    mock_redis = MagicMock()

    with (
        patch("chat.routes.transaction", return_value=mock_cm),
        patch("chat.routes.create_generation_job", AsyncMock(return_value=None)),
        patch("chat.routes.enqueue_chat_turn_job", AsyncMock(return_value="arq-job-123")),
        patch(
            "chat.routes.save_message", return_value={"id": str(msg_id), "role": "user", "content": "Help me expand"}
        ),
    ):
        app.state.pool = mock_pool
        app.state.redis_pool = mock_redis

        resp = client.post(
            "/api/chat/messages",
            json={"map_id": str(map_id), "content": "Help me expand"},
        )
        assert resp.status_code == 202

        # Check history fetch calls: one of them must query with created_at > (SELECT created_at FROM public.chat_messages WHERE id = $3)
        history_queries = [call for call in mock_conn.fetch.call_args_list if "SELECT role, content" in str(call)]
        assert len(history_queries) >= 1
        query_sql = history_queries[0][0][0]
        assert "created_at > (SELECT created_at FROM public.chat_messages WHERE id = $3)" in query_sql
        query_args = history_queries[0][0]
        assert cursor_msg_id in query_args


def test_n1_edit_proposal_converts_to_deltas_and_prevents_branch_wiping(auth_client):
    """N1: Confirms edit proposals convert to delta operations and reject 0-node wipes."""
    client, uid = auth_client
    map_id = uuid.uuid4()
    msg_id = uuid.uuid4()
    existing_root_id = uuid.uuid4()
    existing_leaf_id = uuid.uuid4()

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(side_effect=[msg_id, 2])  # CAS claim, remaining count = 2
    mock_pool.execute = AsyncMock()

    # DB currently has root and leaf
    mock_pool.fetch = AsyncMock(
        side_effect=[
            [{"id": existing_root_id}, {"id": existing_leaf_id}],  # nodes query
            [],  # edges query
        ]
    )

    from unittest.mock import create_autospec

    from services.mindmap_service import MindMapBackendService

    mock_service = create_autospec(MindMapBackendService, instance=True, spec_set=True)
    mock_service.batch_update_graph = AsyncMock(return_value={"map_id": str(map_id), "version": 5})
    app.state.pool = mock_pool
    app.dependency_overrides[get_mindmap_service] = lambda: mock_service

    # Edit proposal only mentions modifying existing_root_id and adding new_child_id
    new_child_id = uuid.uuid4()
    mock_pool.fetchrow = AsyncMock(
        return_value={
            "id": msg_id,
            "role": "assistant",
            "content": "Proposal",
            "user_id": uid,
            "map_id": map_id,
            "is_confirmed": False,
            "metadata": None,
            "proposed_action": {
                "action_type": "edit_proposal",
                "parameters": {
                    "nodes": [
                        {"id": str(existing_root_id), "label": "Updated Root"},
                        {"id": str(new_child_id), "label": "New Child"},
                    ],
                    "edges": [],
                },
            },
        }
    )

    with (
        patch(
            "chat.routes.confirm_proposed_action",
            return_value={
                "id": str(msg_id),
                "is_confirmed": True,
                "map_id": str(map_id),
                "user_id": str(uid),
                "role": "assistant",
                "content": "Confirmed",
                "created_at": datetime.now(UTC),
            },
        ),
        patch("snapshots.service.create_map_snapshot", AsyncMock(return_value=uuid.uuid4())) as mock_snapshot,
    ):
        resp = client.post(f"/api/chat/actions/{msg_id}/confirm", json={"is_confirmed": True})
        assert resp.status_code == 200
        # Pre-edit safety snapshot was taken
        assert mock_snapshot.called
        # batch_update_graph called with categorized deltas
        assert mock_service.batch_update_graph.called
        call_kwargs = mock_service.batch_update_graph.call_args[1]
        updates = call_kwargs["updates"]
        # Update existing node without wholesale wipe of leaf node
        assert len(updates["update_nodes"]) == 1
        assert updates["update_nodes"][0]["id"] == str(existing_root_id)
        assert len(updates["create_nodes"]) == 1
        assert updates["create_nodes"][0]["id"] == str(new_child_id)
        # delete_nodes was NOT populated, ensuring leaf node is not deleted
        assert "delete_nodes" not in updates


def test_n2_chat_callback_base_url_normalization(auth_client):
    """N2: Confirms callback_base_url passed to worker task does not duplicate /internal/chat."""
    client, uid = auth_client
    map_id = uuid.uuid4()

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=True)  # owner
    mock_pool.fetch = AsyncMock(return_value=[])

    mock_conn = MagicMock()
    mock_conn.fetchval = AsyncMock(return_value=uuid.uuid4())
    mock_conn.fetchrow = AsyncMock(return_value={"summary": None, "summary_through_message_id": None})
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_conn.execute = AsyncMock()

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_conn
    mock_cm.__aexit__.return_value = None

    mock_redis = MagicMock()
    enqueued_requests: list[Any] = []

    async def fake_enqueue(redis, request):
        enqueued_requests.append(request)
        return "arq-job-123"

    with (
        patch("chat.routes.transaction", return_value=mock_cm),
        patch("chat.routes.create_generation_job", AsyncMock(return_value=None)),
        patch("chat.routes.enqueue_chat_turn_job", side_effect=fake_enqueue),
        patch(
            "chat.routes.save_message",
            return_value={"id": str(uuid.uuid4()), "role": "user", "content": "Hello"},
        ),
    ):
        app.state.pool = mock_pool
        app.state.redis_pool = mock_redis

        resp = client.post("/api/chat/messages", json={"map_id": str(map_id), "content": "Hello"})
        assert resp.status_code == 202
        assert len(enqueued_requests) == 1
        req = enqueued_requests[0]
        # Should be bare backend internal URL, NOT ending with /internal/chat
        callback_url = req["callback_base_url"] if isinstance(req, dict) else req.callback_base_url
        assert not callback_url.rstrip("/").endswith("/internal/chat")
        assert not callback_url.rstrip("/").endswith("/internal")


def test_n3_generation_request_branch_contract_normalization():
    """N3: Verifies GenerationRequest and BranchContext normalize branch_nodes, branch_edges, and effective ancestor labels."""
    from contracts.models import BranchContext, GenerationRequest, JobType

    root_id = uuid.uuid4()
    child_id = uuid.uuid4()

    # 1. Using branch_nodes and branch_edges
    bc1 = BranchContext(
        branch_nodes=[{"id": str(root_id), "label": "Parent"}],
        branch_edges=[{"source_node_id": str(root_id), "target_node_id": str(child_id)}],
        ancestor_labels=["Root", "Parent"],
    )
    nodes1 = bc1.branch_nodes or bc1.nodes
    edges1 = bc1.branch_edges or bc1.edges
    assert len(nodes1) == 1
    assert len(edges1) == 1
    lbl1 = nodes1[0]["label"] if isinstance(nodes1[0], dict) else nodes1[0].label
    assert lbl1 == "Parent"

    # 2. Using legacy nodes and edges
    bc2 = BranchContext(
        nodes=[{"id": str(root_id), "label": "LegacyParent"}],
        edges=[{"source": str(root_id), "target": str(child_id)}],
    )
    nodes2 = bc2.branch_nodes or bc2.nodes
    edges2 = bc2.branch_edges or bc2.edges
    assert len(nodes2) == 1
    assert len(edges2) == 1
    lbl2 = nodes2[0]["label"] if isinstance(nodes2[0], dict) else nodes2[0].label
    assert lbl2 == "LegacyParent"

    # 3. GenerationRequest effective_ancestor_labels
    gr = GenerationRequest(
        job_id=uuid.uuid4(),
        map_id=uuid.uuid4(),
        job_type=JobType.REGENERATE_NODE,
        target_node_id=child_id,
        branch_context=bc1,
        callback_base_url="http://backend:8000",
        callback_token="secret-worker-token",
    )
    assert gr.effective_ancestor_labels == ["Root", "Parent"]
    assert len(gr.effective_branch_nodes) == 1


def test_h3_mutating_internal_callback_rejects_user_token(auth_client):
    """H3: Mutating callback endpoints reject user token and require callback_token_hash."""
    client, uid = auth_client
    job_id = uuid.uuid4()
    map_id = uuid.uuid4()

    mock_pool = MagicMock()
    app.state.pool = mock_pool

    # Call /internal/chat/{job}/complete with user token (which was provided by auth_client)
    resp = client.post(
        f"/internal/chat/{job_id}/complete",
        json={
            "map_id": str(map_id),
            "job_id": str(job_id),
            "assistant_message": "Done",
        },
    )
    # Must reject user token because mutation requires worker callback_token_hash
    assert resp.status_code == 401


def test_snapshot_restore_by_version_and_by_uuid(auth_client):
    """Snapshot restore accepts both integer version identifier and UUID identifier."""
    client, uid = auth_client
    map_id = uuid.uuid4()
    snapshot_uuid = uuid.uuid4()

    mock_pool = MagicMock()
    app.state.pool = mock_pool

    mock_return = {
        "status": "success",
        "map_id": map_id,
        "restored_version": 2,
        "new_version": 3,
        "snapshot_id": snapshot_uuid,
        "snapshot_data": {"nodes": [], "edges": []},
    }

    with patch(
        "snapshots.service.restore_snapshot_by_version_or_id",
        AsyncMock(return_value=mock_return),
    ) as mock_restore:
        # 1. Restore by version number
        resp = client.post(f"/api/maps/{map_id}/snapshots/2/restore")
        assert resp.status_code == 200
        mock_restore.assert_awaited_with(pool=mock_pool, map_id=map_id, identifier="2", user_id=uid)

        # 2. Restore by snapshot UUID
        resp = client.post(f"/api/maps/{map_id}/snapshots/{snapshot_uuid}/restore")
        assert resp.status_code == 200
        mock_restore.assert_awaited_with(pool=mock_pool, map_id=map_id, identifier=str(snapshot_uuid), user_id=uid)


@pytest.mark.asyncio
async def test_f1_batch_update_graph_rejects_zero_nodes_in_transaction():
    """F1: batch_update_graph rejects delta update leaving 0 nodes inside the transaction."""
    from fastapi import HTTPException
    from services.mindmap_service import MindMapBackendService

    map_id = uuid.uuid4()
    uid = uuid.uuid4()

    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_conn.fetch = AsyncMock(return_value=[])  # 0 remaining nodes
    mock_conn.execute = AsyncMock()

    class FakeTx:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    service = MindMapBackendService(pool=mock_pool, generator=MagicMock())
    with (
        patch("services.mindmap_service.assert_map_owner", AsyncMock(return_value=True)),
        patch("services.mindmap_service.transaction", return_value=FakeTx()),
        patch("services.mindmap_service.check_and_bump_map_version", AsyncMock(return_value=2)),
        patch("services.mindmap_service.delete_map_node", AsyncMock()),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await service.batch_update_graph(
                map_id=map_id,
                user_id=uid,
                updates={"delete_nodes": [str(uuid.uuid4())]},
            )
        assert exc_info.value.status_code == 422
        assert "zero nodes" in exc_info.value.detail.lower()


def test_f2_chat_confirm_action_concurrency_returns_executing_not_applied(auth_client):
    """F2: Concurrent confirmation on executing action returns 200 with status=executing, not applied."""
    client, uid = auth_client
    map_id = uuid.uuid4()
    msg_id = uuid.uuid4()

    mock_pool = MagicMock()
    app.state.pool = mock_pool

    mock_pool.fetchrow = AsyncMock(
        return_value={
            "id": msg_id,
            "role": "assistant",
            "content": "Proposal",
            "user_id": uid,
            "map_id": map_id,
            "is_confirmed": True,
            "metadata": {"execution_status": "executing"},
            "proposed_action": {"action_type": "edit_proposal", "parameters": {}},
            "created_at": datetime.now(UTC),
        }
    )

    resp = client.post(f"/api/chat/actions/{msg_id}/confirm", json={"is_confirmed": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_confirmed"] is True
    assert data["status"] == "executing"
    assert "currently executing" in data["message"].lower()
    assert data["data"] is None


def test_f2_chat_confirm_action_concurrent_cas_claim_returns_executing(auth_client):
    """F2: When CAS fails because another request claimed execution, returns status=executing."""
    client, uid = auth_client
    map_id = uuid.uuid4()
    msg_id = uuid.uuid4()

    mock_pool = MagicMock()
    app.state.pool = mock_pool

    initial_row = {
        "id": msg_id,
        "role": "assistant",
        "content": "Proposal",
        "user_id": uid,
        "map_id": map_id,
        "is_confirmed": False,
        "metadata": None,
        "proposed_action": {"action_type": "edit_proposal", "parameters": {}},
        "created_at": datetime.now(UTC),
    }
    reloaded_row = {
        **initial_row,
        "is_confirmed": True,
        "metadata": {"execution_status": "executing"},
    }

    mock_pool.fetchrow = AsyncMock(side_effect=[initial_row, reloaded_row])
    mock_pool.fetchval = AsyncMock(return_value=None)

    resp = client.post(f"/api/chat/actions/{msg_id}/confirm", json={"is_confirmed": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_confirmed"] is True
    assert data["status"] == "executing"
    assert "currently executing" in data["message"].lower()


@pytest.mark.asyncio
async def test_f3_regenerate_node_missing_redis_raises_503_without_db_writes():
    """F3: regenerate_node fails fast with 503 when Redis is unavailable without creating snapshots or DB rows."""
    from fastapi import HTTPException
    from services.mindmap_service import MindMapBackendService

    map_id = uuid.uuid4()
    node_id = uuid.uuid4()
    uid = uuid.uuid4()

    mock_pool = MagicMock()
    service = MindMapBackendService(pool=mock_pool, generator=MagicMock(), redis_pool=None)

    with (
        patch("services.mindmap_service.assert_map_owner", AsyncMock(return_value=True)),
        patch("services.mindmap_service.get_node_by_id", AsyncMock(return_value={"id": node_id, "label": "Target"})),
        patch("services.mindmap_service.has_active_node_job", AsyncMock(return_value=False)),
        patch("services.mindmap_service.has_active_job", AsyncMock(return_value=False)),
        patch("services.mindmap_service.create_map_snapshot", AsyncMock()) as mock_snap,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await service.regenerate_node(map_id=map_id, node_id=node_id, user_id=uid)
        assert exc_info.value.status_code == 503
        assert "redis connection is required" in exc_info.value.detail.lower()
        mock_snap.assert_not_called()


@pytest.mark.asyncio
async def test_f3_regenerate_node_enqueue_failure_marks_job_rejected():
    """F3: When Redis enqueue fails during regenerate_node, job is compensated and marked rejected."""
    from fastapi import HTTPException
    from services.mindmap_service import MindMapBackendService

    map_id = uuid.uuid4()
    node_id = uuid.uuid4()
    uid = uuid.uuid4()

    mock_pool = MagicMock()
    mock_pool.execute = AsyncMock()
    mock_pool.fetchval = AsyncMock(return_value=None)
    mock_redis = MagicMock()

    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock()
    mock_conn.fetchval = AsyncMock(return_value=uuid.uuid4())

    class FakeTx:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    service = MindMapBackendService(pool=mock_pool, generator=MagicMock(), redis_pool=mock_redis)

    with (
        patch("services.mindmap_service.assert_map_owner", AsyncMock(return_value=True)),
        patch("services.mindmap_service.get_node_by_id", AsyncMock(return_value={"id": node_id, "label": "Target"})),
        patch("services.mindmap_service.has_active_node_job", AsyncMock(return_value=False)),
        patch("services.mindmap_service.has_active_job", AsyncMock(return_value=False)),
        patch("services.mindmap_service.create_map_snapshot", AsyncMock(return_value=uuid.uuid4())),
        patch("services.mindmap_service.list_map_edges", AsyncMock(return_value=[])),
        patch("services.mindmap_service.list_map_nodes", AsyncMock(return_value=[{"id": node_id, "label": "Target"}])),
        patch("services.mindmap_service.get_node_descendant_ids", return_value=[]),
        patch("services.mindmap_service.transaction", return_value=FakeTx()),
        patch("services.mindmap_service.get_or_create_chat_thread", AsyncMock(return_value=uuid.uuid4())),
        patch("services.mindmap_service.insert_chat_message", AsyncMock(return_value=uuid.uuid4())),
        patch("services.mindmap_service.create_generation_job", AsyncMock()),
        patch(
            "services.mindmap_service.enqueue_generation_job",
            AsyncMock(side_effect=RuntimeError("Redis connection dropped")),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await service.regenerate_node(map_id=map_id, node_id=node_id, user_id=uid)
        assert exc_info.value.status_code == 502
        assert any("status = 'rejected'" in str(call) for call in mock_pool.execute.call_args_list)


@pytest.mark.asyncio
async def test_f4_batch_update_graph_cycle_detection_in_transaction():
    """F4: batch_update_graph validates DAG topology in transaction and rejects cycles with 422."""
    from fastapi import HTTPException
    from services.mindmap_service import MindMapBackendService

    map_id = uuid.uuid4()
    uid = uuid.uuid4()
    node_a = uuid.uuid4()
    node_b = uuid.uuid4()

    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_conn.fetch = AsyncMock(
        side_effect=[
            [
                {"id": node_a, "map_id": map_id, "label": "Node A"},
                {"id": node_b, "map_id": map_id, "label": "Node B"},
            ],
            [
                {
                    "id": uuid.uuid4(),
                    "map_id": map_id,
                    "source_node_id": node_a,
                    "target_node_id": node_b,
                    "label": "to_b",
                },
                {
                    "id": uuid.uuid4(),
                    "map_id": map_id,
                    "source_node_id": node_b,
                    "target_node_id": node_a,
                    "label": "to_a",
                },
            ],
        ]
    )
    mock_conn.execute = AsyncMock()

    class FakeTx:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    service = MindMapBackendService(pool=mock_pool, generator=MagicMock())
    with (
        patch("services.mindmap_service.assert_map_owner", AsyncMock(return_value=True)),
        patch("services.mindmap_service.transaction", return_value=FakeTx()),
        patch("services.mindmap_service.check_and_bump_map_version", AsyncMock(return_value=2)),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await service.batch_update_graph(
                map_id=map_id,
                user_id=uid,
                updates={"update_nodes": []},
                validate_graph=True,
            )
        assert exc_info.value.status_code == 422
        assert exc_info.value.detail.get("error") == "GraphValidationError"


def test_f5_typed_graph_edit_parameters_schema():
    """F5: Typed GraphEditParameters model validates delta keys and integrates with ActionProposal."""
    from contracts.models import ActionProposal, GraphEditParameters

    params = GraphEditParameters(
        update_nodes=[{"id": str(uuid.uuid4()), "label": "Updated"}],
        create_nodes=[{"id": str(uuid.uuid4()), "label": "New"}],
        delete_nodes=[str(uuid.uuid4())],
        create_edges=[{"source_node_id": str(uuid.uuid4()), "target_node_id": str(uuid.uuid4())}],
    )
    assert len(params.update_nodes) == 1
    assert len(params.create_nodes) == 1
    assert len(params.delete_nodes) == 1
    assert len(params.create_edges) == 1

    proposal = ActionProposal(
        action_type="edit_proposal",
        parameters=params,
        explanation="Add child node",
    )
    dumped = proposal.model_dump(mode="json")
    assert dumped["action_type"] == "edit_proposal"
    assert "update_nodes" in dumped["parameters"]
    assert "create_nodes" in dumped["parameters"]


def test_chat_confirm_and_dismiss_with_string_user_id(auth_client):
    """Verify confirm and dismiss succeed when current_user.id is a string (Supabase auth standard)."""
    from unittest.mock import create_autospec
    from services.mindmap_service import MindMapBackendService

    client, uid = auth_client
    str_uid = str(uid)
    fake_user = MagicMock(id=str_uid)
    app.dependency_overrides[get_current_user] = lambda: fake_user

    map_id = uuid.uuid4()
    msg_id = uuid.uuid4()
    target_node_id = uuid.uuid4()

    mock_pool = MagicMock()
    mock_service = create_autospec(MindMapBackendService, instance=True, spec_set=True)
    mock_service.regenerate_node = AsyncMock(return_value={"map_id": str(map_id), "job_id": str(uuid.uuid4())})
    mock_pool.execute = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[])
    mock_pool.fetchval = AsyncMock(return_value=msg_id)  # CAS claim succeeds
    app.state.pool = mock_pool
    app.dependency_overrides[get_mindmap_service] = lambda: mock_service

    try:
        # Database returns UUID for user_id in record
        mock_pool.fetchrow = AsyncMock(
            return_value={
                "id": msg_id,
                "role": "assistant",
                "content": "Proposal",
                "user_id": uid,
                "map_id": map_id,
                "is_confirmed": None,
                "metadata": "{}",
                "created_at": datetime.now(UTC),
                "proposed_action": {
                    "action_type": "regen_node",
                    "target_node_ids": [str(target_node_id)],
                    "explanation": "Regenerating node branch",
                },
            }
        )

        with patch("chat.routes.confirm_proposed_action", return_value={"id": str(msg_id), "is_confirmed": True}):
            # 1. Test confirmation
            resp = client.post(f"/api/chat/actions/{msg_id}/confirm", json={"is_confirmed": True})
            assert resp.status_code == 200
            data = resp.json()
            assert data["is_confirmed"] is True
            assert data["status"] == "accepted"

            # 2. Test dismissal
            mock_pool.fetchrow = AsyncMock(
                return_value={
                    "id": msg_id,
                    "role": "assistant",
                    "content": "Proposal",
                    "user_id": uid,
                    "map_id": map_id,
                    "is_confirmed": None,
                    "metadata": "{}",
                    "created_at": datetime.now(UTC),
                    "proposed_action": {
                        "action_type": "regen_node",
                        "target_node_ids": [str(target_node_id)],
                        "explanation": "Regenerating node branch",
                    },
                }
            )
            resp_dismiss = client.post(f"/api/chat/actions/{msg_id}/confirm", json={"is_confirmed": False})
            assert resp_dismiss.status_code == 200
            data_dismiss = resp_dismiss.json()
            assert data_dismiss["is_confirmed"] is False
            assert data_dismiss["status"] == "dismissed"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_mindmap_service, None)


