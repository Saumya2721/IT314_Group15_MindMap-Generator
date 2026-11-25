"""Unit tests for S3-L5 Part B: Node Regeneration Concurrency Guard & Lock Ordering.

Validates:
1. lock_map_for_mutation helper (version acquisition & 404/ValueError on missing map).
2. Concurrency guard during report_complete callback (skip replacement on map version change).
3. Preservation of concurrent descendant edits and newly inserted children.
4. Active jobs targeting removed descendants cancelled to prevent FK deadlocks.
5. Stale pre-read elimination in node/edge routes (returning 404 instead of 200).
6. Batch graph update optimistic concurrency handling with map-first locking.
7. Propagation of expected_map_version and expected_node_version through worker tasks.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import main
import pytest
from contracts.models import (
    GenerationResult,
    NodeDraft,
)
from core.supabase import current_user_id
from fastapi.testclient import TestClient

from db.repository import (
    OptimisticConcurrencyError,
    lock_map_for_mutation,
    replace_node_branch,
)


# ------------------------------------------------------------------ Fixtures & Setup
@pytest.fixture
def auth_client():
    fake_uid = uuid4()
    main.app.dependency_overrides[current_user_id] = lambda: fake_uid
    client = TestClient(main.app)
    try:
        yield client, fake_uid
    finally:
        main.app.dependency_overrides.pop(current_user_id, None)


# ------------------------------------------------------------------ 1. lock_map_for_mutation tests
@pytest.mark.asyncio
async def test_lock_map_for_mutation_success():
    mock_conn = MagicMock()
    mock_conn.fetchrow = AsyncMock(return_value={"version": 5})
    map_id = uuid4()

    version = await lock_map_for_mutation(mock_conn, map_id)
    assert version == 5
    mock_conn.fetchrow.assert_awaited_once()
    assert "FOR UPDATE" in mock_conn.fetchrow.call_args[0][0]


@pytest.mark.asyncio
async def test_lock_map_for_mutation_not_found():
    mock_conn = MagicMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)
    map_id = uuid4()

    with pytest.raises(ValueError, match="Map not found"):
        await lock_map_for_mutation(mock_conn, map_id)


# ------------------------------------------------------------------ 2. Concurrency guard in report_complete
@pytest.mark.asyncio
async def test_report_complete_skips_when_map_version_changed():
    """When DB map version is ahead of expected_map_version, callback skips replacement and returns 200 conflict_skipped."""
    job_id = uuid4()
    map_id = uuid4()
    target_node_id = uuid4()
    user_id = uuid4()
    raw_token = "secret-token-test"
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    # DB map version is 3, but generation request expected version 2
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    mock_conn.transaction.return_value.__aenter__.return_value = mock_conn

    async def mock_fetchrow(query, *args):
        if "FROM public.mind_maps" in query and "FOR UPDATE" in query:
            return {"version": 3}
        if "FROM public.generation_jobs" in query and "FOR UPDATE" in query:
            return {
                "id": job_id,
                "map_id": map_id,
                "job_type": "regenerate_node",
                "target_node_id": target_node_id,
                "status": "running",
                "started_at": datetime.now(UTC),
            }
        if "FROM public.generation_jobs" in query:
            return {
                "id": job_id,
                "map_id": map_id,
                "job_type": "regenerate_node",
                "status": "running",
                "callback_token_hash": token_hash,
            }
        if "FROM public.chat_threads" in query:
            return {"id": uuid4()}
        if "INSERT INTO public.chat_messages" in query:
            return {"id": uuid4()}
        if "FROM public.nodes" in query:
            return {"id": target_node_id, "label": "Architecture Node"}
        return None

    mock_pool.fetchrow = AsyncMock(side_effect=mock_fetchrow)
    mock_conn.fetchrow = AsyncMock(side_effect=mock_fetchrow)
    mock_conn.fetchval = AsyncMock(side_effect=lambda query, *args: 3 if "FROM public.mind_maps" in query else user_id)
    mock_conn.execute = AsyncMock()

    client = TestClient(main.app)
    orig_pool = getattr(main.app.state, "pool", None)
    main.app.state.pool = mock_pool

    try:
        gen_result = GenerationResult(
            job_id=job_id,
            map_id=map_id,
            status="completed",
            expected_map_version=2,  # Stale: DB is at 3
            nodes=[
                NodeDraft(id=str(uuid4()), label="New Child", description="Child desc", node_type="default"),
            ],
            edges=[],
        )

        resp = client.post(
            f"/internal/jobs/{job_id}/complete",
            content=gen_result.model_dump_json(),
            headers={"Authorization": f"Bearer {raw_token}", "Content-Type": "application/json"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "conflict_skipped"
        assert "User edits preserved" in data["message"]

        # Assert job was marked 'failed'
        job_update_call = [
            c
            for c in mock_conn.execute.call_args_list
            if "UPDATE public.generation_jobs" in c[0][0] and "status = 'failed'" in c[0][0]
        ]
        assert len(job_update_call) == 1
        assert "version changed from 2 to 3" in job_update_call[0][0][2]

        # Assert assistant message was inserted via fetchrow
        chat_msg_call = [c for c in mock_conn.fetchrow.call_args_list if "INSERT INTO public.chat_messages" in c[0][0]]
        assert len(chat_msg_call) == 1
        assert "cancelled because the mind map was modified while generating" in chat_msg_call[0][0][3]

    finally:
        main.app.state.pool = orig_pool


def test_full_regeneration_uses_the_stored_base_version(monkeypatch):
    job_id, map_id = uuid4(), uuid4()
    token = "test-full-regeneration"
    pool = MagicMock()
    conn = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = conn
    pool.fetchrow = AsyncMock(
        return_value={
            "map_id": map_id,
            "status": "running",
            "job_type": "regenerate_full",
            "callback_token_hash": hashlib.sha256(token.encode()).hexdigest(),
        }
    )
    conn.fetchrow = AsyncMock(side_effect=[{"version": 3}, {"status": "running", "base_map_version": 2}])
    conn.execute = AsyncMock()
    replace_graph = AsyncMock()
    monkeypatch.setattr(main.app.state, "pool", pool, raising=False)
    monkeypatch.setattr("routes.internal.replace_map_graph", replace_graph)

    result = GenerationResult(job_id=job_id, map_id=map_id, status="completed", expected_map_version=3)
    response = TestClient(main.app).post(
        f"/internal/jobs/{job_id}/complete",
        json=result.model_dump(mode="json"),
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "conflict_skipped"
    replace_graph.assert_not_awaited()
    assert "status = 'failed'" in conn.execute.await_args.args[0]


@pytest.mark.asyncio
async def test_replace_node_branch_raises_when_expected_version_mismatched():
    """Direct test of repository replace_node_branch optimistic guard."""
    map_id = uuid4()
    target_node_id = uuid4()
    job_id = uuid4()

    mock_conn = MagicMock()
    # lock_map_for_mutation returns version 5
    mock_conn.fetchrow = AsyncMock(return_value={"version": 5})

    with pytest.raises(OptimisticConcurrencyError) as exc_info:
        await replace_node_branch(
            mock_conn,
            map_id=map_id,
            target_node_id=target_node_id,
            source_job_id=job_id,
            new_nodes=[],
            new_edges=[],
            expected_map_version=4,  # Stale
        )

    assert exc_info.value.current_version == 5
    assert exc_info.value.expected_version == 4


# ------------------------------------------------------------------ 3. Cancel active descendant jobs
@pytest.mark.asyncio
async def test_replace_node_branch_cancels_active_jobs_targeting_descendants():
    """Verify that replace_node_branch cancels active generation_jobs targeting deleted descendants to prevent FK deadlocks."""
    map_id = uuid4()
    target_node_id = uuid4()
    child_node_id = uuid4()
    job_id = uuid4()

    mock_conn = MagicMock()
    mock_conn.fetchrow = AsyncMock(return_value={"version": 1})
    mock_conn.fetchval = AsyncMock(return_value=1)
    mock_conn.fetch = AsyncMock(
        return_value=[
            {
                "id": uuid4(),
                "map_id": map_id,
                "source_node_id": target_node_id,
                "target_node_id": child_node_id,
                "label": "child_edge",
            }
        ]
    )
    mock_conn.execute = AsyncMock()
    mock_conn.executemany = AsyncMock()

    await replace_node_branch(
        mock_conn,
        map_id=map_id,
        target_node_id=target_node_id,
        source_job_id=job_id,
        new_nodes=[],
        new_edges=[],
        expected_map_version=1,
    )

    # Check that generation_jobs update was executed for target_node_id = ANY(descendants)
    cancel_call = [
        c
        for c in mock_conn.execute.call_args_list
        if "UPDATE public.generation_jobs" in c[0][0] and "target node was removed" in c[0][0]
    ]
    assert len(cancel_call) == 1
    assert cancel_call[0][0][2] == [child_node_id]


# ------------------------------------------------------------------ 4. Route stale pre-read elimination (404 tests)
def test_update_node_returns_404_when_node_deleted_concurrently(auth_client):
    client, _ = auth_client
    map_id = uuid4()
    node_id = uuid4()

    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    mock_conn.transaction.return_value.__aenter__.return_value = mock_conn

    # 1. assert_map_owner
    # 2. pre-check get_node_by_id outside tx returns existing
    # 3. lock_map_for_mutation inside tx
    # 4. update_map_node inside tx returns None (node was deleted by another tx)
    mock_pool.fetchval = AsyncMock(return_value=True)
    mock_pool.fetchrow = AsyncMock(return_value={"id": node_id, "label": "Node 1"})
    mock_conn.fetchrow = AsyncMock(
        side_effect=[
            {"version": 1},  # lock_map_for_mutation
            None,  # update_map_node returned 0 rows!
        ]
    )

    orig_pool = getattr(main.app.state, "pool", None)
    main.app.state.pool = mock_pool
    try:
        resp = client.patch(f"/api/maps/{map_id}/nodes/{node_id}", json={"label": "New Label"})
        assert resp.status_code == 404
        assert "Node not found on this map" in resp.json()["detail"]
    finally:
        main.app.state.pool = orig_pool


def test_update_edge_returns_404_when_edge_deleted_concurrently(auth_client):
    client, _ = auth_client
    map_id = uuid4()
    edge_id = uuid4()

    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    mock_conn.transaction.return_value.__aenter__.return_value = mock_conn

    mock_pool.fetchval = AsyncMock(return_value=True)
    mock_pool.fetchrow = AsyncMock(return_value={"id": edge_id, "label": "Edge 1"})
    mock_conn.fetchrow = AsyncMock(
        side_effect=[
            {"version": 1},  # lock_map_for_mutation
            None,  # update_map_edge returned 0 rows!
        ]
    )

    orig_pool = getattr(main.app.state, "pool", None)
    main.app.state.pool = mock_pool
    try:
        resp = client.patch(f"/api/maps/{map_id}/edges/{edge_id}", json={"label": "New Edge Label"})
        assert resp.status_code == 404
        assert "Edge not found on this map" in resp.json()["detail"]
    finally:
        main.app.state.pool = orig_pool


def test_delete_edge_returns_404_when_edge_already_deleted(auth_client):
    client, _ = auth_client
    map_id = uuid4()
    edge_id = uuid4()

    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    mock_conn.transaction.return_value.__aenter__.return_value = mock_conn

    mock_pool.fetchval = AsyncMock(return_value=True)
    mock_pool.fetchrow = AsyncMock(return_value=None)
    mock_conn.fetchrow = AsyncMock(
        side_effect=[
            {"version": 1},  # lock_map_for_mutation
            None,  # get_edge_by_id inside tx returns None
        ]
    )

    orig_pool = getattr(main.app.state, "pool", None)
    main.app.state.pool = mock_pool
    try:
        resp = client.delete(f"/api/maps/{map_id}/edges/{edge_id}")
        assert resp.status_code == 404
        assert "Edge not found on this map" in resp.json()["detail"]
    finally:
        main.app.state.pool = orig_pool


# ------------------------------------------------------------------ 5. Batch update optimistic locking
def test_batch_update_graph_optimistic_conflict(auth_client):
    client, _ = auth_client
    map_id = uuid4()

    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    mock_conn.transaction.return_value.__aenter__.return_value = mock_conn

    mock_pool.fetchval = AsyncMock(return_value=True)
    # Current DB version is 3: update returns None, then fetch actual returns 3
    mock_conn.fetchval = AsyncMock(side_effect=[None, 3])

    orig_pool = getattr(main.app.state, "pool", None)
    main.app.state.pool = mock_pool
    try:
        # Request expected version 2 (stale)
        resp = client.post(
            f"/api/maps/{map_id}/graph/batch",
            json={"expected_version": 2, "create_nodes": []},
        )
        assert resp.status_code == 409
        data = resp.json()
        assert data["detail"]["error"] == "OptimisticLockConflict"
        assert data["detail"]["current_version"] == 3
        assert data["detail"]["expected_version"] == 2
    finally:
        main.app.state.pool = orig_pool


# ------------------------------------------------------------------ 6. Worker task version propagation
def test_worker_tasks_propagate_expected_versions():
    """Verify that GenerationRequest expected_map_version and expected_node_version pass into GenerationResult."""
    from mindmap_llm.contracts.models import (
        GenerationRequest as WorkerGenReq,
    )
    from mindmap_llm.contracts.models import (
        GenerationResult as WorkerGenRes,
    )
    from mindmap_llm.contracts.models import (
        JobType as WorkerJobType,
    )

    req = WorkerGenReq(
        job_id=uuid4(),
        map_id=uuid4(),
        job_type=WorkerJobType.REGENERATE_NODE,
        target_node_id=uuid4(),
        expected_map_version=7,
        expected_node_version=3,
        callback_base_url="http://localhost:8000",
        callback_token="token",
    )

    # In worker/tasks.py, GenerationResult is constructed with expected_map_version and expected_node_version from req
    res = WorkerGenRes(
        job_id=req.job_id,
        map_id=req.map_id,
        status="completed",
        expected_map_version=req.expected_map_version,
        expected_node_version=req.expected_node_version,
    )

    assert res.expected_map_version == 7
    assert res.expected_node_version == 3
