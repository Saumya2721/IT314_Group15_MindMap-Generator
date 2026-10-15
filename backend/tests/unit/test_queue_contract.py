"""Integration and contract tests for ARQ queue producer, internal callbacks, and node regeneration rejection."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import main
from contracts.models import (
    EdgeDraft,
    FileRef,
    GenerationError,
    GenerationRequest,
    GenerationResult,
    JobType,
    NodeDraft,
)
from core.supabase import current_user_id
from db.models import JobStatus
from db.repository import create_generation_job
from services.generator import compute_layout
from services.queue import enqueue_generation_job


@pytest.mark.asyncio
async def test_arq_producer_enqueues_contract_payload():
    mock_redis = MagicMock()
    mock_job = MagicMock()
    mock_job.job_id = "test-arq-job-123"
    mock_redis.enqueue_job = AsyncMock(return_value=mock_job)

    job_id = uuid.uuid4()
    map_id = uuid.uuid4()
    req = GenerationRequest(
        job_id=job_id,
        map_id=map_id,
        job_type=JobType.GENERATE,
        topic="Neural Networks",
        ideas="Backpropagation, activation functions",
        files=[
            FileRef(
                file_name="paper.pdf",
                mime_type="application/pdf",
                url="https://test.supabase.co/storage/v1/object/sign/paper.pdf",
                storage_key="test-key",
            )
        ],
        callback_base_url="http://localhost:8000",
        callback_token="super-secret-token",
    )

    enqueued_id = await enqueue_generation_job(mock_redis, req.model_dump(mode="json"))

    assert enqueued_id == "test-arq-job-123"
    mock_redis.enqueue_job.assert_awaited_once()
    call_args = mock_redis.enqueue_job.call_args[0]
    assert call_args[0] == "generate_mindmap"
    payload = call_args[1]
    assert payload["job_id"] == str(job_id)
    assert payload["map_id"] == str(map_id)
    assert payload["callback_token"] == "super-secret-token"
    assert payload["callback_base_url"] == "http://localhost:8000"
    assert len(payload["files"]) == 1
    call_kwargs = mock_redis.enqueue_job.call_args[1]
    assert call_kwargs.get("_job_id") == str(job_id)


@pytest.mark.asyncio
async def test_create_generation_job_string_and_enum():
    """Verify create_generation_job accepts both string and JobType enum without AttributeError."""
    from datetime import timezone

    async def mock_fetchrow(query, *args):
        j_type = "generate"
        for a in args:
            if a in ("generate", "regenerate_full", "regenerate_node"):
                j_type = a
                break
        return {
            "id": uuid.uuid4(),
            "map_id": uuid.uuid4(),
            "job_type": j_type,
            "status": "pending",
            "progress_percent": 0,
            "error_message": None,
            "created_at": datetime.now(timezone.utc),
            "started_at": None,
            "completed_at": None,
            "triggered_by_message_id": None,
            "target_node_id": None,
            "callback_token_hash": "dummy",
        }

    mock_conn = MagicMock()
    mock_conn.fetchrow = AsyncMock(side_effect=mock_fetchrow)

    # 1. String job_type
    job_str = await create_generation_job(
        mock_conn,
        map_id=uuid.uuid4(),
        job_type="generate",
        callback_token_hash="hash1",
    )
    assert job_str.job_type == "generate"

    # 2. Enum job_type
    job_enum = await create_generation_job(
        mock_conn,
        map_id=uuid.uuid4(),
        job_type=JobType.REGENERATE_FULL,
        callback_token_hash="hash2",
    )
    assert job_enum.job_type in (JobType.REGENERATE_FULL, "regenerate_full")


def test_reject_node_regeneration_intake_501():
    client = TestClient(main.app)
    fake_uid = uuid.uuid4()
    map_id = uuid.uuid4()
    node_id = uuid.uuid4()

    mock_pool = MagicMock()
    # User is owner
    mock_pool.fetchval = AsyncMock(side_effect=[True, True])
    main.app.state.pool = mock_pool

    main.app.dependency_overrides[current_user_id] = lambda: fake_uid

    try:
        resp = client.post(f"/api/maps/{map_id}/nodes/{node_id}/regenerate")
        assert resp.status_code == 501
        assert "not yet supported" in resp.json()["detail"].lower()
    finally:
        main.app.dependency_overrides.pop(current_user_id, None)


def test_reject_node_regeneration_unauthorized_map():
    client = TestClient(main.app)
    fake_uid = uuid.uuid4()
    map_id = uuid.uuid4()
    node_id = uuid.uuid4()

    mock_pool = MagicMock()
    # User is NOT owner
    mock_pool.fetchval = AsyncMock(return_value=False)
    main.app.state.pool = mock_pool

    main.app.dependency_overrides[current_user_id] = lambda: fake_uid

    try:
        resp = client.post(f"/api/maps/{map_id}/nodes/{node_id}/regenerate")
        assert resp.status_code == 404
    finally:
        main.app.dependency_overrides.pop(current_user_id, None)


def test_readiness_health_endpoint():
    client = TestClient(main.app)
    resp = client.get("/api/health/ready")
    assert resp.status_code in (200, 503)
    data = resp.json()
    assert "status" in data
    assert "database" in data
    assert "redis" in data


@pytest.mark.asyncio
async def test_null_callback_token_hash_rejected_401():
    """P0 Security: If callback_token_hash is NULL, reject callback rather than allowing all tokens."""
    job_id = uuid.uuid4()

    mock_pool = MagicMock()
    mock_pool.fetchrow = AsyncMock(
        return_value={
            "id": job_id,
            "status": "pending",
            "progress_percent": 0,
            "callback_token_hash": None,  # NULL hash!
        }
    )

    orig_pool = getattr(main.app.state, "pool", None)
    main.app.state.pool = mock_pool

    client = TestClient(main.app)
    try:
        resp = client.post(
            f"/internal/jobs/{job_id}/progress",
            json={"percent": 50},
            headers={"Authorization": "Bearer any-token-value"},
        )
        assert resp.status_code == 401
    finally:
        main.app.state.pool = orig_pool


@pytest.mark.asyncio
async def test_internal_callback_auth_verification():
    job_id = uuid.uuid4()
    raw_token = "valid-secret-token-123"
    expected_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    mock_pool = MagicMock()
    mock_pool.fetchrow = AsyncMock(
        return_value={
            "id": job_id,
            "status": "pending",
            "progress_percent": 10,
            "callback_token_hash": expected_hash,
        }
    )
    mock_pool.execute = AsyncMock(return_value="UPDATE 1")

    # Set mock pool on app.state
    orig_pool = getattr(main.app.state, "pool", None)
    main.app.state.pool = mock_pool

    client = TestClient(main.app)
    try:
        # 1. Missing Authorization header -> 401
        resp = client.post(f"/internal/jobs/{job_id}/progress", json={"percent": 50})
        assert resp.status_code == 401

        # 2. Invalid Bearer token -> 401 Unauthorized
        resp = client.post(
            f"/internal/jobs/{job_id}/progress",
            json={"percent": 50},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401

        # 3. Valid Bearer token -> 204 No Content
        resp = client.post(
            f"/internal/jobs/{job_id}/progress",
            json={"percent": 50},
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        assert resp.status_code == 204
    finally:
        main.app.state.pool = orig_pool


@pytest.mark.asyncio
async def test_internal_complete_callback_strict_validation():
    job_id = uuid.uuid4()
    map_id = uuid.uuid4()
    raw_token = "valid-token-complete"
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    mock_pool = MagicMock()
    mock_pool.fetchrow = AsyncMock(
        return_value={
            "id": job_id,
            "map_id": map_id,
            "job_type": "generate",
            "status": "running",
            "callback_token_hash": token_hash,
        }
    )

    client = TestClient(main.app)
    orig_pool = getattr(main.app.state, "pool", None)
    main.app.state.pool = mock_pool

    try:
        # Wrong job_id in body -> 400
        bad_result = GenerationResult(
            job_id=uuid.uuid4(),  # Different from path job_id
            map_id=map_id,
            status="completed",
        )
        resp = client.post(
            f"/internal/jobs/{job_id}/complete",
            content=bad_result.model_dump_json(),
            headers={"Authorization": f"Bearer {raw_token}", "Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert "job_id mismatch" in resp.json()["detail"]

        # Wrong map_id in body -> 400
        bad_result_map = GenerationResult(
            job_id=job_id,
            map_id=uuid.uuid4(),  # Different map_id
            status="completed",
        )
        resp = client.post(
            f"/internal/jobs/{job_id}/complete",
            content=bad_result_map.model_dump_json(),
            headers={"Authorization": f"Bearer {raw_token}", "Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert "map_id mismatch" in resp.json()["detail"]

        # Status not 'completed' on complete endpoint -> 400
        bad_status_result = GenerationResult(
            job_id=job_id,
            map_id=map_id,
            status="failed",
        )
        resp = client.post(
            f"/internal/jobs/{job_id}/complete",
            content=bad_status_result.model_dump_json(),
            headers={"Authorization": f"Bearer {raw_token}", "Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert "Invalid status" in resp.json()["detail"]
    finally:
        main.app.state.pool = orig_pool


def test_compute_layout_handles_worker_edge_keys():
    """Verify compute_layout handles edge dicts with source_node_id and target_node_id."""
    root_id = str(uuid.uuid4())
    c1_id = str(uuid.uuid4())
    c2_id = str(uuid.uuid4())

    nodes = [
        {"id": root_id, "label": "Root", "node_type": "root", "pos_x": 0.0, "pos_y": 0.0},
        {"id": c1_id, "label": "Child 1", "pos_x": 0.0, "pos_y": 0.0},
        {"id": c2_id, "label": "Child 2", "pos_x": 0.0, "pos_y": 0.0},
    ]
    # Edge dicts using worker contract keys: source_node_id and target_node_id
    edges = [
        {"id": str(uuid.uuid4()), "source_node_id": root_id, "target_node_id": c1_id, "label": None},
        {"id": str(uuid.uuid4()), "source_node_id": root_id, "target_node_id": c2_id, "label": None},
    ]

    compute_layout(nodes, edges)

    # Root should be at center
    assert nodes[0]["pos_x"] == 450.0
    assert nodes[0]["pos_y"] == 300.0

    # Children should have radiating positions
    assert nodes[1]["pos_x"] != 0.0 or nodes[1]["pos_y"] != 0.0
    assert nodes[2]["pos_x"] != 0.0 or nodes[2]["pos_y"] != 0.0
    assert (nodes[1]["pos_x"], nodes[1]["pos_y"]) != (nodes[2]["pos_x"], nodes[2]["pos_y"])


def _build_test_pool(fetchrow_side_effect=None, fetchval_side_effect=None):
    from datetime import timezone
    mock_conn = MagicMock()
    if fetchrow_side_effect is not None:
        mock_conn.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        mock_conn.fetchrow = AsyncMock(
            return_value={
                "id": uuid.uuid4(),
                "map_id": uuid.uuid4(),
                "triggered_by_message_id": None,
                "job_type": "generate",
                "target_node_id": None,
                "status": "pending",
                "progress_percent": 0,
                "error_message": None,
                "created_at": datetime.now(timezone.utc),
                "started_at": None,
                "completed_at": None,
                "callback_token_hash": "dummy_hash",
            }
        )

    if fetchval_side_effect is not None:
        mock_conn.fetchval = AsyncMock(side_effect=fetchval_side_effect)
    else:
        mock_conn.fetchval = AsyncMock(return_value=None)

    mock_tx = MagicMock()
    mock_tx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction = MagicMock(return_value=mock_tx)

    mock_conn.execute = AsyncMock()

    mock_acquire = MagicMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=mock_acquire)
    pool.fetchrow = mock_conn.fetchrow
    pool.fetchval = mock_conn.fetchval
    pool.execute = mock_conn.execute
    return pool


@pytest.mark.asyncio
async def test_late_complete_on_failed_job_returns_409():
    """Verify that if a job already reached terminal status 'failed' or 'rejected', late completion returns 409."""
    from datetime import timezone
    job_id = uuid.uuid4()
    map_id = uuid.uuid4()
    raw_token = "valid-token-terminal"
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    mock_pool = _build_test_pool(
        fetchrow_side_effect=[
            {
                "id": job_id,
                "map_id": map_id,
                "job_type": "generate",
                "status": "failed",
                "callback_token_hash": token_hash,
            },
            # SELECT FOR UPDATE in transaction:
            {
                "id": job_id,
                "map_id": map_id,
                "job_type": "generate",
                "status": "failed",
                "started_at": datetime.now(timezone.utc),
            },
        ]
    )

    client = TestClient(main.app)
    orig_pool = getattr(main.app.state, "pool", None)
    main.app.state.pool = mock_pool

    try:
        res = GenerationResult(
            job_id=job_id,
            map_id=map_id,
            status="completed",
            nodes=[NodeDraft(id=str(uuid.uuid4()), label="Root", description="Root node", node_type="root")],
            edges=[],
        )
        resp = client.post(
            f"/internal/jobs/{job_id}/complete",
            content=res.model_dump_json(),
            headers={"Authorization": f"Bearer {raw_token}", "Content-Type": "application/json"},
        )
        assert resp.status_code == 409
        assert "Cannot complete job" in resp.json()["detail"]
    finally:
        main.app.state.pool = orig_pool


@pytest.mark.asyncio
async def test_late_fail_on_completed_job_returns_409():
    """Verify that if a job already reached terminal status 'completed', a conflicting fail callback returns 409."""
    from datetime import timezone
    job_id = uuid.uuid4()
    map_id = uuid.uuid4()
    raw_token = "valid-token-terminal-fail"
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    mock_pool = _build_test_pool(
        fetchrow_side_effect=[
            {
                "id": job_id,
                "map_id": map_id,
                "job_type": "generate",
                "status": "completed",
                "callback_token_hash": token_hash,
            },
            {
                "id": job_id,
                "status": "completed",
                "started_at": datetime.now(timezone.utc),
            },
        ]
    )

    client = TestClient(main.app)
    orig_pool = getattr(main.app.state, "pool", None)
    main.app.state.pool = mock_pool

    try:
        res = GenerationResult(
            job_id=job_id,
            map_id=map_id,
            status="failed",
            error=GenerationError(error_type="engine_error", stage="generate", message="Late failure"),
        )
        resp = client.post(
            f"/internal/jobs/{job_id}/fail",
            content=res.model_dump_json(),
            headers={"Authorization": f"Bearer {raw_token}", "Content-Type": "application/json"},
        )
        assert resp.status_code == 409
        assert "Cannot fail job" in resp.json()["detail"]
    finally:
        main.app.state.pool = orig_pool


def test_unified_routes_fail_503_when_redis_unavailable():
    """Verify quick-generate and generate endpoints fail with 503 when Redis is unavailable rather than using disparate fallback."""
    client = TestClient(main.app)
    fake_uid = uuid.uuid4()
    map_id = uuid.uuid4()

    mock_pool = _build_test_pool(
        fetchval_side_effect=[True, None, False],
    )

    orig_pool = getattr(main.app.state, "pool", None)
    orig_redis = getattr(main.app.state, "redis_pool", None)
    main.app.state.pool = mock_pool
    main.app.state.redis_pool = None  # Redis DOWN!

    main.app.dependency_overrides[current_user_id] = lambda: fake_uid

    try:
        # 1. quick-generate without Redis -> 503
        resp1 = client.post("/api/maps/quick-generate", json={"topic": "Quantum Computing"})
        assert resp1.status_code == 503
        assert "Redis connection is required" in resp1.json()["detail"]

        # 2. generate without Redis -> 503
        resp2 = client.post(
            f"/api/maps/{map_id}/generate",
            data={"topic": "Quantum Computing"},
        )
        assert resp2.status_code == 503
        assert "Redis connection is required" in resp2.json()["detail"]
    finally:
        main.app.dependency_overrides.pop(current_user_id, None)
        main.app.state.pool = orig_pool
        main.app.state.redis_pool = orig_redis


@pytest.mark.asyncio
async def test_store_files_partial_failure_compensates_cleaned_keys():
    """Verify that if a second file fails during upload, previously uploaded files in the batch are removed."""
    from fastapi import HTTPException
    from uploads.service import store_files
    import core.supabase

    mock_storage = MagicMock()
    # First upload succeeds, second upload throws exception
    mock_storage.upload.side_effect = [None, RuntimeError("Supabase storage network glitch")]
    mock_storage.remove = MagicMock(return_value=None)
    mock_storage.create_signed_url = MagicMock(return_value={"signedURL": "https://example.com/file0"})

    orig_from = core.supabase.admin.storage.from_
    core.supabase.admin.storage.from_ = MagicMock(return_value=mock_storage)

    uid = uuid.uuid4()
    map_id = uuid.uuid4()
    job_id = uuid.uuid4()
    items = [
        ("file0.pdf", "application/pdf", b"%PDF-1.4 ... file 0"),
        ("file1.pdf", "application/pdf", b"%PDF-1.4 ... file 1"),
    ]

    try:
        with pytest.raises(HTTPException) as exc_info:
            await store_files(uid, map_id, job_id, items)
        assert exc_info.value.status_code == 502
        # Verify compensating removal was called with file 0's key
        mock_storage.remove.assert_called_once()
        cleaned_keys = mock_storage.remove.call_args[0][0]
        assert len(cleaned_keys) == 1
        assert f"{uid}/{map_id}/{job_id}/0_file0.pdf" == cleaned_keys[0]
    finally:
        core.supabase.admin.storage.from_ = orig_from


def test_generate_queue_failure_triggers_storage_cleanup_and_job_rejection():
    """Verify that when queue dispatch fails after files are uploaded:
    1. Supabase Storage deletes uploaded files (compensation).
    2. DB generation job is transitioned to 'rejected' (chk_generation_job_lifecycle compliant).
    3. Endpoint returns 502 with error details.
    """
    from unittest.mock import patch
    import core.supabase

    client = TestClient(main.app)
    fake_uid = uuid.uuid4()
    map_id = uuid.uuid4()

    mock_pool = _build_test_pool(
        fetchval_side_effect=[True, None, False],  # owner check passes, no existing nodes, no active job
    )

    mock_storage = MagicMock()
    mock_storage.remove = MagicMock(return_value=None)
    orig_from = core.supabase.admin.storage.from_
    core.supabase.admin.storage.from_ = MagicMock(return_value=mock_storage)

    mock_redis = MagicMock()

    orig_pool = getattr(main.app.state, "pool", None)
    orig_redis = getattr(main.app.state, "redis_pool", None)
    main.app.state.pool = mock_pool
    main.app.state.redis_pool = mock_redis

    main.app.dependency_overrides[current_user_id] = lambda: fake_uid

    try:
        with patch("maps.routes.read_uploads", new_callable=AsyncMock, return_value=[("doc.pdf", "application/pdf", b"%PDF-1.4 ...")]), \
             patch("maps.routes.store_files", new_callable=AsyncMock) as mock_store, \
             patch("maps.routes.enqueue_generation_job", new_callable=AsyncMock, side_effect=RuntimeError("Redis queue cluster crashed")):

            from uploads.service import UploadedFile
            mock_store.return_value = [
                UploadedFile(
                    file_name="doc.pdf",
                    mime_type="application/pdf",
                    data=b"%PDF-1.4 ...",
                    storage_key=f"{fake_uid}/{map_id}/test_job/0_doc.pdf",
                    signed_url="https://example.com/signed",
                )
            ]

            resp = client.post(
                f"/api/maps/{map_id}/generate",
                data={"topic": "Microservices"},
                files={"file": ("doc.pdf", b"%PDF-1.4 ...", "application/pdf")},
            )
            assert resp.status_code == 502
            assert "Failed to initiate generation job" in resp.json()["detail"]

            # 1. Compensation: Storage removal called
            mock_storage.remove.assert_called_once_with([f"{fake_uid}/{map_id}/test_job/0_doc.pdf"])

            # 2. Compensation: DB job transitioned to 'rejected'
            execute_calls = mock_pool.execute.call_args_list
            assert len(execute_calls) >= 1
            query, *params = execute_calls[-1][0]
            assert "status = 'rejected'" in query
            assert "completed_at = now()" in query
            assert "started_at IS NULL" in query
    finally:
        main.app.dependency_overrides.pop(current_user_id, None)
        main.app.state.pool = orig_pool
        main.app.state.redis_pool = orig_redis
        core.supabase.admin.storage.from_ = orig_from


def test_quick_generate_enqueue_failure_triggers_job_rejection():
    """Verify that if Redis enqueue throws an exception during quick_generate:
    1. DB generation job is transitioned to 'rejected' (chk_generation_job_lifecycle compliant).
    2. Endpoint returns 503 instead of leaving job permanently pending.
    """
    from unittest.mock import patch
    client = TestClient(main.app)
    fake_uid = uuid.uuid4()

    mock_pool = _build_test_pool()
    mock_redis = MagicMock()

    orig_pool = getattr(main.app.state, "pool", None)
    orig_redis = getattr(main.app.state, "redis_pool", None)
    main.app.state.pool = mock_pool
    main.app.state.redis_pool = mock_redis

    main.app.dependency_overrides[current_user_id] = lambda: fake_uid

    try:
        with patch("maps.routes.enqueue_generation_job", new_callable=AsyncMock, side_effect=RuntimeError("Redis connection dropped")):
            resp = client.post("/api/maps/quick-generate", json={"topic": "Distributed Systems"})
            assert resp.status_code == 503
            assert "Failed to enqueue generation job" in resp.json()["detail"]

            # Verify compensating DB execution
            execute_calls = mock_pool.execute.call_args_list
            assert len(execute_calls) >= 1
            query, *params = execute_calls[-1][0]
            assert "status = 'rejected'" in query
            assert "completed_at = now()" in query
            assert "started_at IS NULL" in query
    finally:
        main.app.dependency_overrides.pop(current_user_id, None)
        main.app.state.pool = orig_pool
        main.app.state.redis_pool = orig_redis


def test_internal_job_status_check_endpoint():
    """Verify GET /internal/jobs/{job_id} returns terminal state and receipt for pre-flight worker check."""
    job_id = uuid.uuid4()
    map_id = uuid.uuid4()
    raw_token = "valid-token-receipt"
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    mock_pool = _build_test_pool(
        fetchrow_side_effect=[
            {
                "id": job_id,
                "map_id": map_id,
                "job_type": "generate",
                "status": "completed",
                "callback_token_hash": token_hash,
                "progress_percent": 100,
            }
        ]
    )

    client = TestClient(main.app)
    orig_pool = getattr(main.app.state, "pool", None)
    main.app.state.pool = mock_pool

    try:
        resp = client.get(
            f"/internal/jobs/{job_id}",
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(job_id)
        assert data["status"] == "completed"
        assert data["already_terminal"] is True
        assert data["receipt_id"] == str(job_id)
    finally:
        main.app.state.pool = orig_pool
