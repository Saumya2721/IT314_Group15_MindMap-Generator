"""Unit tests for Sprint 2 Issue S2-BE2: Node Comments & Regeneration API.

Covers:
- Node comments CRUD (create, list, update, delete, permissions)
- Branch subgraph resolution (isolated subtree & incoming parent edge)
- Map snapshots (creation, listing, detail, restore with version bump)
- Node regeneration intake (job tracking, active job conflict, instruction resolution, snapshots)
- Internal completion callback with partial branch replacement (replace_node_branch)
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from core.supabase import current_user_id
from fastapi.testclient import TestClient
from main import app


# ------------------------------------------------------------------ Fixtures & Helpers
@pytest.fixture
def auth_client():
    fake_uid = uuid4()
    app.dependency_overrides[current_user_id] = lambda: fake_uid
    client = TestClient(app)
    try:
        yield client, fake_uid
    finally:
        app.dependency_overrides.pop(current_user_id, None)


# ------------------------------------------------------------------ Comments CRUD Tests
def test_create_comment_success(auth_client):
    client, uid = auth_client
    map_id = uuid4()
    node_id = uuid4()
    comment_id = uuid4()

    mock_pool = MagicMock()
    # is_owner check
    mock_pool.fetchval = AsyncMock(return_value=True)

    async def mock_fetchrow(query, *args):
        if "FROM public.nodes" in query:
            return {"id": node_id, "map_id": map_id, "label": "Node 1"}
        if "INSERT INTO public.node_comments" in query:
            return {
                "id": comment_id,
                "map_id": map_id,
                "node_id": node_id,
                "user_id": uid,
                "content": "Please expand this concept with details.",
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            }
        return None

    mock_pool.fetchrow = AsyncMock(side_effect=mock_fetchrow)
    app.state.pool = mock_pool

    resp = client.post(
        f"/api/maps/{map_id}/nodes/{node_id}/comments",
        json={"content": "Please expand this concept with details."},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] == str(comment_id)
    assert data["content"] == "Please expand this concept with details."
    assert data["node_id"] == str(node_id)
    assert data["user_id"] == str(uid)


def test_create_comment_empty_content_validation(auth_client):
    client, _ = auth_client
    map_id = uuid4()
    node_id = uuid4()

    resp = client.post(
        f"/api/maps/{map_id}/nodes/{node_id}/comments",
        json={"content": "   "},
    )
    assert resp.status_code == 422


def test_create_comment_node_not_found(auth_client):
    client, _ = auth_client
    map_id = uuid4()
    node_id = uuid4()

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=True)
    mock_pool.fetchrow = AsyncMock(return_value=None)
    app.state.pool = mock_pool

    resp = client.post(
        f"/api/maps/{map_id}/nodes/{node_id}/comments",
        json={"content": "A comment on a non-existent node"},
    )
    assert resp.status_code == 404
    assert "node not found" in resp.json()["detail"].lower()


def test_list_comments_success(auth_client):
    client, uid = auth_client
    map_id = uuid4()
    node_id = uuid4()
    c1_id = uuid4()
    c2_id = uuid4()

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=True)
    mock_pool.fetchrow = AsyncMock(return_value={"id": node_id, "map_id": map_id})

    now = datetime.now(UTC)
    mock_pool.fetch = AsyncMock(
        return_value=[
            {
                "id": c1_id,
                "map_id": map_id,
                "node_id": node_id,
                "user_id": uid,
                "content": "First instruction",
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": c2_id,
                "map_id": map_id,
                "node_id": node_id,
                "user_id": uid,
                "content": "Second instruction",
                "created_at": now,
                "updated_at": now,
            },
        ]
    )
    app.state.pool = mock_pool

    resp = client.get(f"/api/maps/{map_id}/nodes/{node_id}/comments")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["id"] == str(c1_id)
    assert data[0]["content"] == "First instruction"
    assert data[1]["id"] == str(c2_id)


def test_update_comment_success_and_forbidden_check(auth_client):
    client, uid = auth_client
    map_id = uuid4()
    node_id = uuid4()
    comment_id = uuid4()
    other_user_id = uuid4()

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=True)

    now = datetime.now(UTC)

    # 1. Non-author edit attempt (forbidden)
    async def mock_fetchrow_forbidden(query, *args):
        if "FROM public.nodes" in query:
            return {"id": node_id, "map_id": map_id}
        if "FROM public.node_comments" in query:
            return {
                "id": comment_id,
                "map_id": map_id,
                "node_id": node_id,
                "user_id": other_user_id,
                "content": "Original comment",
                "created_at": now,
                "updated_at": now,
            }
        return None

    mock_pool.fetchrow = AsyncMock(side_effect=mock_fetchrow_forbidden)
    app.state.pool = mock_pool

    resp = client.patch(
        f"/api/maps/{map_id}/nodes/{node_id}/comments/{comment_id}",
        json={"content": "Attempted edit by another user"},
    )
    assert resp.status_code == 403
    assert "permission" in resp.json()["detail"].lower()

    # 2. Author edit success
    async def mock_fetchrow_success(query, *args):
        if "FROM public.nodes" in query:
            return {"id": node_id, "map_id": map_id}
        if "SELECT id, map_id, node_id, user_id, content" in query:
            return {
                "id": comment_id,
                "map_id": map_id,
                "node_id": node_id,
                "user_id": uid,
                "content": "Original comment",
                "created_at": now,
                "updated_at": now,
            }
        if "UPDATE public.node_comments" in query:
            return {
                "id": comment_id,
                "map_id": map_id,
                "node_id": node_id,
                "user_id": uid,
                "content": "Updated comment content",
                "created_at": now,
                "updated_at": now,
            }
        return None

    mock_pool.fetchrow = AsyncMock(side_effect=mock_fetchrow_success)
    resp = client.patch(
        f"/api/maps/{map_id}/nodes/{node_id}/comments/{comment_id}",
        json={"content": "Updated comment content"},
    )
    assert resp.status_code == 200
    assert resp.json()["content"] == "Updated comment content"


def test_delete_comment_author_and_map_owner(auth_client):
    client, uid = auth_client
    map_id = uuid4()
    node_id = uuid4()
    comment_id = uuid4()

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=True)

    now = datetime.now(UTC)
    mock_pool.fetchrow = AsyncMock(
        side_effect=[
            {"id": node_id, "map_id": map_id},
            {
                "id": comment_id,
                "map_id": map_id,
                "node_id": node_id,
                "user_id": uid,
                "content": "To be deleted",
                "created_at": now,
                "updated_at": now,
            },
        ]
    )
    mock_pool.execute = AsyncMock(return_value="DELETE 1")
    app.state.pool = mock_pool

    resp = client.delete(f"/api/maps/{map_id}/nodes/{node_id}/comments/{comment_id}")
    assert resp.status_code == 204


# ------------------------------------------------------------------ Branch Subgraph Resolution
def test_get_node_branch_resolution(auth_client):
    client, _ = auth_client
    map_id = uuid4()
    root_id = uuid4()
    target_node_id = uuid4()
    child_id = uuid4()
    grandchild_id = uuid4()
    unrelated_id = uuid4()

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=True)

    async def mock_fetchrow(query, *args):
        if "public.nodes" in query:
            return {"id": target_node_id, "map_id": map_id, "label": "Target Node"}
        return None

    mock_pool.fetchrow = AsyncMock(side_effect=mock_fetchrow)

    now = datetime.now(UTC)
    nodes = [
        {"id": root_id, "map_id": map_id, "label": "Root", "created_at": now, "updated_at": now},
        {"id": target_node_id, "map_id": map_id, "label": "Target Node", "created_at": now, "updated_at": now},
        {"id": child_id, "map_id": map_id, "label": "Child Node", "created_at": now, "updated_at": now},
        {"id": grandchild_id, "map_id": map_id, "label": "Grandchild Node", "created_at": now, "updated_at": now},
        {"id": unrelated_id, "map_id": map_id, "label": "Unrelated Branch", "created_at": now, "updated_at": now},
    ]
    edges = [
        {
            "id": uuid4(),
            "map_id": map_id,
            "source_node_id": root_id,
            "target_node_id": target_node_id,
            "label": "to_target",
            "created_at": now,
        },
        {
            "id": uuid4(),
            "map_id": map_id,
            "source_node_id": target_node_id,
            "target_node_id": child_id,
            "label": "to_child",
            "created_at": now,
        },
        {
            "id": uuid4(),
            "map_id": map_id,
            "source_node_id": child_id,
            "target_node_id": grandchild_id,
            "label": "to_grandchild",
            "created_at": now,
        },
        {
            "id": uuid4(),
            "map_id": map_id,
            "source_node_id": root_id,
            "target_node_id": unrelated_id,
            "label": "to_other",
            "created_at": now,
        },
    ]

    async def mock_fetch(query, *args):
        if "public.nodes" in query:
            return nodes
        if "public.edges" in query:
            return edges
        return []

    mock_pool.fetch = AsyncMock(side_effect=mock_fetch)
    app.state.pool = mock_pool

    resp = client.get(f"/api/maps/{map_id}/nodes/{target_node_id}/branch")
    assert resp.status_code == 200
    data = resp.json()
    assert data["map_id"] == str(map_id)
    assert data["target_node_id"] == str(target_node_id)
    assert set(data["descendant_ids"]) == {str(child_id), str(grandchild_id)}
    assert data["node_count"] == 3  # target, child, grandchild
    assert data["edge_count"] == 2  # target->child, child->grandchild
    assert data["parent_edge"] is not None
    assert data["parent_edge"]["source_node_id"] == str(root_id)
    assert data["parent_edge"]["label"] == "to_target"


# ------------------------------------------------------------------ Snapshots API Tests
def test_create_and_list_snapshots(auth_client):
    client, _ = auth_client
    map_id = uuid4()
    snap_id = uuid4()

    mock_pool = MagicMock()

    async def mock_fetchval(query, *args):
        if "INSERT INTO public.map_snapshots" in query:
            return snap_id
        return True

    mock_pool.fetchval = AsyncMock(side_effect=mock_fetchval)
    now = datetime.now(UTC)
    mock_pool.fetch = AsyncMock(
        return_value=[
            {
                "id": snap_id,
                "map_id": map_id,
                "trigger_job_id": None,
                "target_node_id": None,
                "reason": "Manual backup",
                "nodes_count": 5,
                "edges_count": 4,
                "created_at": now,
            }
        ]
    )
    app.state.pool = mock_pool

    # 1. Create snapshot
    resp = client.post(
        f"/api/maps/{map_id}/snapshots",
        json={"reason": "Manual backup"},
    )
    assert resp.status_code == 201
    assert resp.json()["id"] == str(snap_id)

    # 2. List snapshots
    resp_list = client.get(f"/api/maps/{map_id}/snapshots")
    assert resp_list.status_code == 200
    items = resp_list.json()
    assert len(items) == 1
    assert items[0]["id"] == str(snap_id)
    assert items[0]["nodes_count"] == 5


def test_get_and_restore_snapshot(auth_client):
    client, _ = auth_client
    map_id = uuid4()
    snap_id = uuid4()
    safety_snap_id = uuid4()

    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

    now = datetime.now(UTC)
    snapshot_row = {
        "id": snap_id,
        "map_id": map_id,
        "trigger_job_id": None,
        "target_node_id": None,
        "reason": "Before edit",
        "nodes_snapshot": [{"id": str(uuid4()), "label": "Node A", "pos_x": 0.0, "pos_y": 0.0}],
        "edges_snapshot": [],
        "created_at": now,
    }

    # 1. Get snapshot detail
    mock_pool.fetchval = AsyncMock(return_value=True)
    mock_pool.fetchrow = AsyncMock(return_value=snapshot_row)
    app.state.pool = mock_pool

    resp = client.get(f"/api/maps/{map_id}/snapshots/{snap_id}")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["id"] == str(snap_id)
    assert len(detail["nodes_snapshot"]) == 1
    assert detail["nodes_snapshot"][0]["label"] == "Node A"

    # 2. Restore snapshot
    mock_conn.transaction.return_value.__aenter__.return_value = mock_conn
    mock_conn.fetchval = AsyncMock(side_effect=[safety_snap_id, 4])  # safety snapshot id, then version 4
    mock_conn.fetchrow = AsyncMock(return_value=snapshot_row)
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_conn.execute = AsyncMock()
    mock_conn.executemany = AsyncMock()

    resp_restore = client.post(f"/api/maps/{map_id}/snapshots/{snap_id}/restore")
    assert resp_restore.status_code == 200
    res_data = resp_restore.json()
    assert res_data["status"] == "restored"
    assert res_data["snapshot_id"] == str(snap_id)
    assert res_data["version"] == 4
    assert res_data["nodes_restored"] == 1

    # 3. Restore snapshot via direct alias route
    mock_conn.fetchval = AsyncMock(side_effect=[safety_snap_id, 5])
    mock_pool.fetchrow = AsyncMock(return_value={"map_id": map_id, **snapshot_row})
    resp_restore_direct = client.post(f"/api/snapshots/{snap_id}/restore")
    assert resp_restore_direct.status_code == 200
    assert resp_restore_direct.json()["status"] == "restored"
    assert resp_restore_direct.json()["version"] == 5


# ------------------------------------------------------------------ Node Regeneration Intake Tests
def test_node_regeneration_active_job_conflict(auth_client):
    client, _ = auth_client
    map_id = uuid4()
    node_id = uuid4()

    mock_pool = MagicMock()
    # is_owner -> True
    mock_pool.fetchval = AsyncMock(side_effect=[True, True])  # is_owner, then has_active_node_job -> True
    mock_pool.fetchrow = AsyncMock(return_value={"id": node_id, "map_id": map_id, "label": "Target Node"})
    app.state.pool = mock_pool

    resp = client.post(
        f"/api/maps/{map_id}/nodes/{node_id}/regenerate",
        json={"instruction": "Regenerate this"},
    )
    assert resp.status_code == 409
    assert "already in progress" in resp.json()["detail"].lower()


def test_node_regeneration_resolves_instruction_from_comment(auth_client):
    client, _ = auth_client
    map_id = uuid4()
    node_id = uuid4()
    comment_id = uuid4()

    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

    now = datetime.now(UTC)
    target_node = {
        "id": node_id,
        "map_id": map_id,
        "label": "AI Architecture",
        "description": "System design",
        "pos_x": 10.0,
        "pos_y": 20.0,
        "background_color": None,
        "text_color": None,
        "width": 120.0,
        "height": 60.0,
        "node_type": "default",
        "source_job_id": None,
        "node_data": None,
        "created_at": now,
        "updated_at": now,
    }
    comment_row = {
        "id": comment_id,
        "map_id": map_id,
        "node_id": node_id,
        "user_id": uuid4(),
        "content": "Break down into microservices and event brokers",
        "created_at": now,
        "updated_at": now,
    }

    async def mock_fetchrow(query, *args):
        if "public.nodes" in query:
            return target_node
        if "public.node_comments" in query:
            return comment_row
        return {"id": uuid4()}

    mock_pool.fetchrow = AsyncMock(side_effect=mock_fetchrow)
    mock_pool.fetchval = AsyncMock(side_effect=[True, None, None, uuid4()])  # owner, no node job, no map job, snapshot
    mock_pool.fetch = AsyncMock(return_value=[])

    mock_redis = MagicMock()
    mock_job = MagicMock()
    mock_job.job_id = "arq-job-123"
    mock_redis.enqueue_job = AsyncMock(return_value=mock_job)
    app.state.pool = mock_pool
    app.state.redis_pool = mock_redis

    job_row = {
        "id": uuid4(),
        "map_id": map_id,
        "job_type": "regenerate_node",
        "status": "pending",
        "progress_percent": 0,
        "error_message": None,
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "triggered_by_message_id": None,
        "target_node_id": node_id,
        "callback_token_hash": "dummy",
    }

    async def mock_conn_fetchrow(query, *args):
        if "generation_jobs" in query:
            return job_row
        return {"id": uuid4()}

    mock_conn.transaction.return_value.__aenter__.return_value = mock_conn
    mock_conn.fetchrow = AsyncMock(side_effect=mock_conn_fetchrow)
    mock_conn.fetchval = AsyncMock(return_value=uuid4())
    mock_conn.execute = AsyncMock()

    resp = client.post(
        f"/api/maps/{map_id}/nodes/{node_id}/regenerate",
        json={"comment_id": str(comment_id)},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "accepted"
    assert data["target_node_id"] == str(node_id)

    # Verify enqueued payload received the comment's instruction
    mock_redis.enqueue_job.assert_awaited_once()
    call_args = mock_redis.enqueue_job.call_args[0]
    payload = call_args[1]
    assert payload["regeneration_instruction"] == "Break down into microservices and event brokers"
    assert payload["target_node_id"] == str(node_id)
    assert payload["branch_context"]["target_node_label"] == "AI Architecture"


# ------------------------------------------------------------------ Internal Completion Callback Test
@pytest.mark.asyncio
async def test_internal_complete_regenerates_only_affected_descendants():
    from db.models import EdgeRecord, NodeRecord
    from db.repository import replace_node_branch

    map_id = uuid4()
    job_id = uuid4()
    root_id = uuid4()
    target_node_id = uuid4()
    old_child_id = uuid4()
    new_child_id = uuid4()
    other_node_id = uuid4()

    existing_edges = [
        {
            "id": uuid4(),
            "map_id": map_id,
            "source_node_id": root_id,
            "target_node_id": target_node_id,
            "label": "root_to_target",
        },
        {
            "id": uuid4(),
            "map_id": map_id,
            "source_node_id": target_node_id,
            "target_node_id": old_child_id,
            "label": "old_branch",
        },
        {
            "id": uuid4(),
            "map_id": map_id,
            "source_node_id": root_id,
            "target_node_id": other_node_id,
            "label": "other_branch",
        },
    ]

    mock_conn = MagicMock()
    mock_conn.fetch = AsyncMock(return_value=existing_edges)
    mock_conn.fetchval = AsyncMock(return_value=2)  # version
    mock_conn.execute = AsyncMock()
    mock_conn.executemany = AsyncMock()

    new_nodes = [
        NodeRecord(
            id=new_child_id,
            map_id=map_id,
            label="Newly Generated Child",
            description="Detail",
            pos_x=10.0,
            pos_y=20.0,
            background_color=None,
            text_color=None,
            node_type="default",
            source_job_id=job_id,
        )
    ]
    new_edges = [
        EdgeRecord(
            id=uuid4(),
            map_id=map_id,
            source_node_id=target_node_id,
            target_node_id=new_child_id,
            label="new_branch_link",
        )
    ]

    await replace_node_branch(
        mock_conn,
        map_id=map_id,
        target_node_id=target_node_id,
        source_job_id=job_id,
        new_nodes=new_nodes,
        new_edges=new_edges,
    )

    # 1. Assert old child deleted by UUID array
    mock_conn.execute.assert_any_call(
        "DELETE FROM public.nodes WHERE map_id = $1 AND id = ANY($2::uuid[])",
        map_id,
        [old_child_id],
    )

    # 2. Assert outgoing edges from target_node_id deleted
    mock_conn.execute.assert_any_call(
        "DELETE FROM public.edges WHERE map_id = $1 AND source_node_id = $2",
        map_id,
        target_node_id,
    )

    # 3. Assert new branch nodes inserted
    mock_conn.executemany.assert_any_call(
        mock_conn.executemany.call_args_list[0][0][0],
        [
            (
                new_child_id,
                map_id,
                "Newly Generated Child",
                "Detail",
                10.0,
                20.0,
                None,
                None,
                "default",
                job_id,
                None,
            )
        ],
    )

    # 4. Assert new branch edges inserted
    assert mock_conn.executemany.call_count == 2
