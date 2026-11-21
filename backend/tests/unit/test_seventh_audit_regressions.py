"""Regression and integration tests covering the 7th audit remediations (U1, U3, U4, U5, R2, L8)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from services.mindmap_service import MindMapBackendService


class FakeTx:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


@pytest.mark.asyncio
async def test_full_regeneration_redis_enqueue_failure_unlocks_map():
    """U1 / R1: Verify that when Redis enqueue fails during regenerate_full,
    the job is transitioned to 'rejected' using the updated compensation query,
    leaving the map unlocked.
    """
    map_id = uuid.uuid4()
    uid = uuid.uuid4()
    job_id = uuid.uuid4()

    mock_pool = MagicMock()
    mock_pool.execute = AsyncMock()
    mock_pool.fetchval = AsyncMock(return_value=None)
    mock_redis = MagicMock()

    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock()

    fake_job = MagicMock(id=job_id)

    service = MindMapBackendService(pool=mock_pool, generator=MagicMock(), redis_pool=mock_redis)

    with (
        patch("services.mindmap_service.assert_map_owner", AsyncMock(return_value=True)),
        patch("services.mindmap_service.transaction", return_value=FakeTx(mock_conn)),
        patch("services.mindmap_service.get_or_create_chat_thread", AsyncMock(return_value=uuid.uuid4())),
        patch("services.mindmap_service.insert_chat_message", AsyncMock(return_value=uuid.uuid4())),
        patch("services.mindmap_service.create_generation_job", AsyncMock(return_value=fake_job)),
        patch("services.mindmap_service.create_map_snapshot", AsyncMock(return_value=uuid.uuid4())),
        patch("services.mindmap_service.update_job_status", AsyncMock()) as mock_update_status,
        patch(
            "services.mindmap_service.enqueue_generation_job",
            AsyncMock(side_effect=RuntimeError("Redis connection dropped")),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await service.regenerate_full(
                map_id=map_id,
                user_id=uid,
                instruction="Full rewrite",
            )

        assert exc_info.value.status_code == 503
        assert "queue service is unavailable" in exc_info.value.detail.lower()

        # Job was NOT prematurely set to RUNNING before queue dispatch
        mock_update_status.assert_not_called()

        # Compensation query was executed with status in ('pending', 'running')
        mock_pool.execute.assert_called_once()
        call_args = mock_pool.execute.call_args[0]
        sql = call_args[0]
        assert "status = 'rejected'" in sql
        assert "status IN ('pending', 'running')" in sql
        assert call_args[1] == job_id


@pytest.mark.asyncio
async def test_full_regeneration_atomic_snapshot_and_job_creation():
    """U3 / R8: Verify that create_generation_job and create_map_snapshot
    are called inside the exact same database transaction.
    """
    map_id = uuid.uuid4()
    uid = uuid.uuid4()
    job_id = uuid.uuid4()

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=None)
    mock_redis = MagicMock()

    mock_conn = MagicMock()
    fake_job = MagicMock(id=job_id)

    service = MindMapBackendService(pool=mock_pool, generator=MagicMock(), redis_pool=mock_redis)

    with (
        patch("services.mindmap_service.assert_map_owner", AsyncMock(return_value=True)),
        patch("services.mindmap_service.transaction", return_value=FakeTx(mock_conn)),
        patch("services.mindmap_service.get_or_create_chat_thread", AsyncMock(return_value=uuid.uuid4())),
        patch("services.mindmap_service.insert_chat_message", AsyncMock(return_value=uuid.uuid4())),
        patch("services.mindmap_service.create_generation_job", AsyncMock(return_value=fake_job)) as mock_create_job,
        patch("services.mindmap_service.create_map_snapshot", AsyncMock(return_value=uuid.uuid4())) as mock_create_snap,
        patch("services.mindmap_service.enqueue_generation_job", AsyncMock(return_value="enqueued-123")),
    ):
        res = await service.regenerate_full(map_id=map_id, user_id=uid, instruction="Expand map")
        assert res["status"] == "enqueued"
        assert res["job_id"] == str(job_id)

        # Both functions received the same transactional connection
        assert mock_create_job.call_args[0][0] is mock_conn
        assert mock_create_snap.call_args[0][0] is mock_conn
        assert mock_create_snap.call_args[1]["trigger_job_id"] == job_id


@pytest.mark.asyncio
async def test_node_regeneration_atomic_snapshot_and_job_creation():
    """U4 / R8: Verify that regenerate_node creates the safety snapshot
    and generation job atomically in the same database transaction.
    """
    map_id = uuid.uuid4()
    node_id = uuid.uuid4()
    uid = uuid.uuid4()

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=None)
    mock_redis = MagicMock()

    mock_conn = MagicMock()

    service = MindMapBackendService(pool=mock_pool, generator=MagicMock(), redis_pool=mock_redis)

    with (
        patch("services.mindmap_service.assert_map_owner", AsyncMock(return_value=True)),
        patch("services.mindmap_service.get_node_by_id", AsyncMock(return_value={"id": node_id, "label": "Target"})),
        patch("services.mindmap_service.has_active_node_job", AsyncMock(return_value=False)),
        patch("services.mindmap_service.has_active_job", AsyncMock(return_value=False)),
        patch("services.mindmap_service.list_map_edges", AsyncMock(return_value=[])),
        patch("services.mindmap_service.list_map_nodes", AsyncMock(return_value=[{"id": node_id, "label": "Target"}])),
        patch("services.mindmap_service.get_node_descendant_ids", return_value=[]),
        patch("services.mindmap_service.transaction", return_value=FakeTx(mock_conn)),
        patch("services.mindmap_service.get_or_create_chat_thread", AsyncMock(return_value=uuid.uuid4())),
        patch("services.mindmap_service.insert_chat_message", AsyncMock(return_value=uuid.uuid4())),
        patch("services.mindmap_service.create_generation_job", AsyncMock()) as mock_create_job,
        patch("services.mindmap_service.create_map_snapshot", AsyncMock(return_value=uuid.uuid4())) as mock_create_snap,
        patch("services.mindmap_service.enqueue_generation_job", AsyncMock(return_value="enqueued-456")),
    ):
        res = await service.regenerate_node(map_id=map_id, node_id=node_id, user_id=uid)
        assert res["status"] == "accepted"
        assert res["target_node_id"] == str(node_id)

        # Both created on the same connection
        assert mock_create_job.call_args[0][0] is mock_conn
        assert mock_create_snap.call_args[0][0] is mock_conn
        assert mock_create_snap.call_args[1]["target_node_id"] == node_id


@pytest.mark.asyncio
async def test_create_edge_without_id_generates_uuid():
    """R2: Proposal creating an edge without an ID generates a server-side UUID."""
    map_id = uuid.uuid4()
    uid = uuid.uuid4()
    node_a = uuid.uuid4()
    node_b = uuid.uuid4()

    mock_pool = MagicMock()
    mock_pool.fetch = AsyncMock(return_value=[])
    mock_conn = MagicMock()
    mock_conn.fetchval = AsyncMock(return_value=2)
    mock_conn.fetch = AsyncMock(return_value=[{"id": node_a}, {"id": node_b}])  # non-empty remaining nodes

    service = MindMapBackendService(pool=mock_pool, generator=MagicMock())

    with (
        patch("services.mindmap_service.assert_map_owner", AsyncMock(return_value=True)),
        patch("services.mindmap_service.transaction", return_value=FakeTx(mock_conn)),
        patch("services.mindmap_service.check_and_bump_map_version", AsyncMock(return_value=2)),
        patch(
            "services.mindmap_service.list_map_nodes",
            AsyncMock(return_value=[{"id": node_a}, {"id": node_b}]),
        ),
        patch("services.mindmap_service.create_map_edge", AsyncMock()) as mock_create_edge,
    ):
        await service.batch_update_graph(
            map_id=map_id,
            user_id=uid,
            updates={
                "create_edges": [{"source_node_id": str(node_a), "target_node_id": str(node_b), "label": "connects"}]
            },
            validate_graph=False,
        )
        mock_create_edge.assert_called_once()
        edge_id = mock_create_edge.call_args[1]["edge_id"]
        assert isinstance(edge_id, uuid.UUID)


@pytest.mark.asyncio
async def test_create_edge_with_malformed_id_returns_422():
    """R2 / L8: Proposal supplying a malformed edge ID raises HTTP 422 instead of silent replacement."""
    map_id = uuid.uuid4()
    uid = uuid.uuid4()
    node_a = uuid.uuid4()
    node_b = uuid.uuid4()

    mock_pool = MagicMock()
    mock_pool.fetch = AsyncMock(return_value=[])
    mock_conn = MagicMock()
    mock_conn.fetchval = AsyncMock(return_value=2)

    service = MindMapBackendService(pool=mock_pool, generator=MagicMock())

    with (
        patch("services.mindmap_service.assert_map_owner", AsyncMock(return_value=True)),
        patch("services.mindmap_service.transaction", return_value=FakeTx(mock_conn)),
        patch("services.mindmap_service.check_and_bump_map_version", AsyncMock(return_value=2)),
        patch(
            "services.mindmap_service.list_map_nodes",
            AsyncMock(return_value=[{"id": node_a}, {"id": node_b}]),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await service.batch_update_graph(
                map_id=map_id,
                user_id=uid,
                updates={
                    "create_edges": [
                        {
                            "id": "not-a-valid-uuid-string",
                            "source_node_id": str(node_a),
                            "target_node_id": str(node_b),
                        }
                    ]
                },
            )
        assert exc_info.value.status_code == 422
        assert "must be a valid uuid" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_create_edge_cross_map_endpoint_rejected_422():
    """L8: Edge referencing a node that does not exist on this map is rejected with HTTP 422."""
    map_id = uuid.uuid4()
    uid = uuid.uuid4()
    node_a = uuid.uuid4()
    other_map_node = uuid.uuid4()

    mock_pool = MagicMock()
    mock_pool.fetch = AsyncMock(return_value=[])
    mock_conn = MagicMock()
    mock_conn.fetchval = AsyncMock(return_value=2)

    service = MindMapBackendService(pool=mock_pool, generator=MagicMock())

    with (
        patch("services.mindmap_service.assert_map_owner", AsyncMock(return_value=True)),
        patch("services.mindmap_service.transaction", return_value=FakeTx(mock_conn)),
        patch("services.mindmap_service.check_and_bump_map_version", AsyncMock(return_value=2)),
        patch(
            "services.mindmap_service.list_map_nodes",
            AsyncMock(return_value=[{"id": node_a}]),  # other_map_node is NOT on this map
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await service.batch_update_graph(
                map_id=map_id,
                user_id=uid,
                updates={
                    "create_edges": [
                        {
                            "source_node_id": str(node_a),
                            "target_node_id": str(other_map_node),
                        }
                    ]
                },
            )
        assert exc_info.value.status_code == 422
        assert "does not exist on this map" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_create_edge_with_alias_endpoint_succeeds():
    """R2: Edge referencing a newly created node alias ('alias_node_1') resolves correctly."""
    map_id = uuid.uuid4()
    uid = uuid.uuid4()
    node_a = uuid.uuid4()

    mock_pool = MagicMock()
    mock_pool.fetch = AsyncMock(return_value=[])
    mock_conn = MagicMock()
    mock_conn.fetchval = AsyncMock(return_value=2)
    mock_conn.fetch = AsyncMock(return_value=[{"id": node_a}])

    service = MindMapBackendService(pool=mock_pool, generator=MagicMock())

    with (
        patch("services.mindmap_service.assert_map_owner", AsyncMock(return_value=True)),
        patch("services.mindmap_service.transaction", return_value=FakeTx(mock_conn)),
        patch("services.mindmap_service.check_and_bump_map_version", AsyncMock(return_value=2)),
        patch("services.mindmap_service.create_map_node", AsyncMock()),
        patch(
            "services.mindmap_service.list_map_nodes",
            AsyncMock(return_value=[{"id": node_a}]),
        ),
        patch("services.mindmap_service.create_map_edge", AsyncMock()) as mock_create_edge,
    ):
        await service.batch_update_graph(
            map_id=map_id,
            user_id=uid,
            updates={
                "create_nodes": [{"id": "alias_1", "label": "Newly Created Child"}],
                "create_edges": [{"source": str(node_a), "target": "alias_1", "label": "has_child"}],
            },
            validate_graph=False,
        )
        mock_create_edge.assert_called_once()
        target_id = mock_create_edge.call_args[1]["target_node_id"]
        assert isinstance(target_id, uuid.UUID)
        assert target_id != node_a


@pytest.mark.asyncio
async def test_empty_graph_root_fallback_no_index_error():
    """U5 / R5: Creating a node on an empty graph evaluates parent_id to None without raising IndexError."""
    from maps.schemas import NodeCreate

    map_id = uuid.uuid4()
    uid = uuid.uuid4()
    node_id = uuid.uuid4()

    mock_pool = MagicMock()
    mock_pool.fetch = AsyncMock(return_value=[])
    mock_conn = MagicMock()
    mock_conn.fetchval = AsyncMock(return_value=2)

    body = NodeCreate(
        label="Root Node",
        pos_x=0.0,
        pos_y=0.0,
    )

    with (
        patch("maps.routes.pool_of", return_value=mock_pool),
        patch("maps.routes.transaction", return_value=FakeTx(mock_conn)),
        patch("maps.routes.assert_map_owner", AsyncMock(return_value=True)),
        patch("maps.routes.list_map_nodes", AsyncMock(return_value=[])),  # empty map!
        patch("maps.routes.list_map_edges", AsyncMock(return_value=[])),
        patch(
            "maps.routes.create_map_node",
            AsyncMock(
                return_value={
                    "id": node_id,
                    "map_id": map_id,
                    "label": "Root Node",
                    "description": None,
                    "pos_x": 0.0,
                    "pos_y": 0.0,
                    "background_color": "#ffffff",
                    "text_color": "#000000",
                    "width": 160.0,
                    "height": 60.0,
                    "node_type": "concept",
                    "node_data": {},
                    "created_at": "2026-11-16T00:00:00Z",
                    "updated_at": "2026-11-16T00:00:00Z",
                }
            ),
        ) as mock_create_node,
        patch("maps.routes.create_map_edge", AsyncMock()) as mock_create_edge,
    ):
        from maps.routes import create_node_route

        res = await create_node_route(map_id=map_id, body=body, request=MagicMock(), uid=uid)
        assert res["label"] == "Root Node"
        mock_create_node.assert_called_once()
        # Edge creation is skipped because parent_id is None
        mock_create_edge.assert_not_called()
