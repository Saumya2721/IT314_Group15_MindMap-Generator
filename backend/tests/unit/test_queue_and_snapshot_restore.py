from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from contracts.models import (
    ChatTurnRequest,
    ChatTurnResult,
    JobType,
    NodeDraft,
    RegenerateNodeRequest,
    RegenerateNodeResult,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from services.queue import enqueue_chat_turn_job, enqueue_node_regeneration_job
from snapshots.routes import router as snapshots_router


def test_contracts_models_instantiation():
    map_id = uuid.uuid4()
    job_id = uuid.uuid4()
    node_id = uuid.uuid4()

    # 1. RegenerateNodeRequest
    regen_req = RegenerateNodeRequest(
        job_id=job_id,
        map_id=map_id,
        target_node_id=node_id,
        instruction="Expand details",
        callback_base_url="https://api.test",
        callback_token="token-123",
    )
    assert regen_req.job_type == JobType.REGENERATE_NODE
    assert regen_req.target_node_id == node_id

    # 2. RegenerateNodeResult
    regen_res = RegenerateNodeResult(
        job_id=job_id,
        map_id=map_id,
        target_node_id=node_id,
        status="completed",
        replacement_node=NodeDraft(id=node_id, label="Expanded Node", description="Detailed description"),
    )
    assert regen_res.status == "completed"
    assert regen_res.replacement_node is not None
    assert regen_res.replacement_node.label == "Expanded Node"

    # 3. ChatTurnRequest
    chat_req = ChatTurnRequest(
        job_id=job_id,
        map_id=map_id,
        message="Add a node about quantum computing",
        callback_base_url="https://api.test",
        callback_token="token-123",
    )
    assert chat_req.job_type == JobType.CHAT_TURN
    assert chat_req.message == "Add a node about quantum computing"

    # 4. ChatTurnResult
    chat_res = ChatTurnResult(
        job_id=job_id,
        map_id=map_id,
        status="completed",
        assistant_message="I have proposed adding a node for quantum computing.",
        proposed_action={"type": "add_node", "label": "Quantum Computing"},
    )
    assert chat_res.status == "completed"
    assert chat_res.proposed_action["label"] == "Quantum Computing"


def test_queue_dispatchers_support_new_job_types():
    async def _test():
        mock_redis = MagicMock()
        mock_job = MagicMock()
        mock_job.job_id = "job-node-1"
        mock_redis.enqueue_job = AsyncMock(return_value=mock_job)

        # Test node regeneration dispatch
        payload_node = {
            "job_id": str(uuid.uuid4()),
            "map_id": str(uuid.uuid4()),
            "target_node_id": str(uuid.uuid4()),
        }
        res_node = await enqueue_node_regeneration_job(mock_redis, payload_node)
        assert res_node == "job-node-1"
        assert payload_node["job_type"] == "regenerate_node"

        # Test chat turn dispatch
        mock_job.job_id = "job-chat-1"
        payload_chat = {
            "job_id": str(uuid.uuid4()),
            "map_id": str(uuid.uuid4()),
            "message": "Hello",
        }
        res_chat = await enqueue_chat_turn_job(mock_redis, payload_chat)
        assert res_chat == "job-chat-1"
        assert payload_chat["job_type"] == "chat_turn"

    import asyncio

    asyncio.run(_test())


def test_snapshot_restore_by_version_endpoint():
    app = FastAPI()
    app.include_router(snapshots_router)
    client = TestClient(app)

    map_id = uuid.uuid4()
    version_no = 2
    mock_uid = uuid.uuid4()

    mock_pool = MagicMock()
    app.state.pool = mock_pool

    with patch(
        "snapshots.routes.restore_snapshot_by_version_or_id",
        AsyncMock(
            return_value={
                "status": "restored",
                "map_id": str(map_id),
                "restored_version": version_no,
                "new_version": 3,
                "snapshot_id": str(uuid.uuid4()),
                "snapshot_data": {
                    "nodes": [{"id": str(uuid.uuid4()), "label": "Restored Root"}],
                    "edges": [],
                },
            }
        ),
    ):
        from core.supabase import current_user_id

        app.dependency_overrides[current_user_id] = lambda: mock_uid

        resp = client.post(f"/maps/{map_id}/snapshots/{version_no}/restore")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "restored"
        assert data["restored_version"] == 2
        assert data["new_version"] == 3
        assert len(data["snapshot_data"]["nodes"]) == 1
        assert data["snapshot_data"]["nodes"][0]["label"] == "Restored Root"


def test_snapshot_service_restore_snapshot_data_to_map():
    import asyncio

    from snapshots.service import (
        get_snapshot_by_version,
        restore_snapshot_by_version_or_id,
        restore_snapshot_data_to_map,
    )

    async def _test():
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock()
        mock_conn.executemany = AsyncMock()
        mock_conn.fetchrow = AsyncMock(
            return_value={
                "id": uuid.uuid4(),
                "map_id": uuid.uuid4(),
                "version_number": 1,
                "snapshot_data": {"nodes": [], "edges": []},
            }
        )

        map_id = uuid.uuid4()
        snap_data = {
            "nodes": [{"id": str(uuid.uuid4()), "label": "Node 1", "pos_x": 10.0, "pos_y": 20.0}],
            "edges": [
                {"id": str(uuid.uuid4()), "source_node_id": str(uuid.uuid4()), "target_node_id": str(uuid.uuid4())}
            ],
        }

        # Test restore_snapshot_data_to_map with dict
        res = await restore_snapshot_data_to_map(mock_conn, map_id, snap_data)
        assert res == snap_data
        assert mock_conn.execute.call_count == 2
        assert mock_conn.executemany.call_count == 2

        # Test get_snapshot_by_version
        snap_row = await get_snapshot_by_version(mock_conn, map_id, 1)
        assert snap_row is not None
        assert snap_row["version_number"] == 1

        # Test restore_snapshot_by_version_or_id
        mock_pool = MagicMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_conn
        mock_cm.__aexit__.return_value = None

        with (
            patch("snapshots.service.transaction", return_value=mock_cm),
            patch("snapshots.service.assert_map_owner", AsyncMock(return_value=True)),
            patch(
                "snapshots.service.get_snapshot_by_version",
                AsyncMock(return_value={"version_number": 1, "snapshot_data": snap_data}),
            ),
            patch("snapshots.service.restore_snapshot_data_to_map", AsyncMock(return_value=snap_data)),
            patch(
                "snapshots.service.create_map_snapshot",
                AsyncMock(return_value={"version_number": 2, "id": uuid.uuid4()}),
            ),
        ):
            user_id = uuid.uuid4()
            outcome = await restore_snapshot_by_version_or_id(mock_pool, map_id, "1", user_id)
            assert outcome is not None
            assert outcome["status"] == "restored"
            assert outcome["restored_version"] == 1
            assert outcome["new_version"] == 2

    asyncio.run(_test())
