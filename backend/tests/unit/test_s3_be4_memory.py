"""Unit tests for S3-BE4 Memory API.

Covers:
- GET /memory (all memories, paginated)
- GET /memory?map_id=... (map-scoped)
- PATCH /memory/{id} (edit content, disable/enable)
- DELETE /memory/{id} (delete memory)
- GDPR: GET /memory/export (JSON bundle of all user memories)
- Worker-proposal write path: POST /internal/memory/propose
  (validates, dedupes, enforces per-user limit, stores)
- Ownership checks on all routes
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from auth.dependencies import require_internal_service
from core.config import settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from memories.routes import internal_router as internal_memory_router
from memories.routes import memory_owner, worker_memory_owner
from memories.routes import router as memory_router
from memories.schemas import MemoryPatchRequest, MemoryProposeRequest
from memories.service import (
    delete_memory,
    gdpr_export,
    list_memories,
    patch_memory,
    propose_memory,
)

# ---------------------------------------------------------------------------
# Fixtures and Test App
# ---------------------------------------------------------------------------


TEST_USER_ID = UUID("11111111-1111-1111-1111-111111111111")
OTHER_USER_ID = UUID("22222222-2222-2222-2222-222222222222")
TEST_MAP_ID = UUID("33333333-3333-3333-3333-333333333333")
OTHER_MAP_ID = UUID("44444444-4444-4444-4444-444444444444")


@pytest.fixture
def mock_pool():
    pool = MagicMock()
    pool.fetchval = AsyncMock()
    pool.fetchrow = AsyncMock()
    pool.fetch = AsyncMock()
    pool.execute = AsyncMock()
    pool.acquire.return_value.__aenter__.return_value = pool
    return pool


@pytest.fixture
def client(mock_pool):
    app = FastAPI()
    app.state.pool = mock_pool
    # Override current_user_id to simulate authenticated TEST_USER_ID
    app.dependency_overrides[memory_owner] = lambda: TEST_USER_ID
    app.dependency_overrides[require_internal_service] = lambda: {"auth": "internal_service"}
    app.dependency_overrides[worker_memory_owner] = lambda: (TEST_USER_ID, TEST_MAP_ID)

    app.include_router(memory_router)
    app.include_router(internal_memory_router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. Service Layer Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_memories_service(mock_pool):
    now = datetime.now(UTC)
    mock_pool.fetchval.return_value = 1
    mock_pool.fetch.return_value = [
        {
            "id": uuid4(),
            "user_id": TEST_USER_ID,
            "map_id": None,
            "memory_type": "user_preference",
            "content": "User prefers concise mindmaps",
            "is_active": True,
            "created_at": now,
        }
    ]

    items, total = await list_memories(mock_pool, user_id=TEST_USER_ID, limit=10, offset=0)
    assert total == 1
    assert len(items) == 1
    assert items[0]["content"] == "User prefers concise mindmaps"
    assert items[0]["is_active"] is True


@pytest.mark.asyncio
async def test_patch_memory_service(mock_pool):
    mem_id = uuid4()
    now = datetime.now(UTC)
    mock_pool.fetchrow.return_value = {
        "id": mem_id,
        "user_id": TEST_USER_ID,
        "map_id": None,
        "memory_type": "user_preference",
        "content": "Updated content",
        "is_active": False,
        "created_at": now,
    }

    payload = MemoryPatchRequest(content="Updated content", is_active=False)
    res = await patch_memory(mock_pool, memory_id=mem_id, user_id=TEST_USER_ID, payload=payload)
    assert res is not None
    assert res["content"] == "Updated content"
    assert res["is_active"] is False


@pytest.mark.asyncio
async def test_delete_memory_service(mock_pool):
    mem_id = uuid4()
    mock_pool.execute.return_value = "DELETE 1"

    deleted = await delete_memory(mock_pool, memory_id=mem_id, user_id=TEST_USER_ID)
    assert deleted is True

    mock_pool.execute.return_value = "DELETE 0"
    deleted_fail = await delete_memory(mock_pool, memory_id=mem_id, user_id=TEST_USER_ID)
    assert deleted_fail is False


@pytest.mark.asyncio
async def test_gdpr_export_service(mock_pool):
    now = datetime.now(UTC)
    mock_pool.fetch.return_value = [
        {
            "id": uuid4(),
            "map_id": None,
            "memory_type": "preference",
            "content": "GDPR export test",
            "is_active": True,
            "created_at": now,
        }
    ]

    export = await gdpr_export(mock_pool, user_id=TEST_USER_ID)
    assert export["user_id"] == str(TEST_USER_ID)
    assert export["total_memories"] == 1
    assert export["memories"][0]["content"] == "GDPR export test"
    assert "GDPR Article 15" in export["notice"]


@pytest.mark.asyncio
async def test_propose_memory_limit_exceeded(mock_pool):
    # Simulate user has reached 500 rows limit
    mock_pool.fetchval.return_value = 500

    payload = MemoryProposeRequest(
        user_id=TEST_USER_ID,
        memory_type="summary",
        content="New knowledge to remember",
    )
    res = await propose_memory(mock_pool, payload)
    assert res.stored is False
    assert res.reason == "limit_exceeded"


@pytest.mark.asyncio
async def test_propose_memory_exact_duplicate(mock_pool):
    existing_id = uuid4()
    # 1. count < 500
    # 2. exact match found
    mock_pool.fetchval.side_effect = [10, existing_id]

    payload = MemoryProposeRequest(
        user_id=TEST_USER_ID,
        memory_type="preference",
        content="Prefer dark mode architectural diagrams",
    )
    res = await propose_memory(mock_pool, payload)
    assert res.stored is False
    assert res.reason == "duplicate"
    assert res.id == existing_id


@pytest.mark.asyncio
async def test_propose_memory_near_duplicate(mock_pool):
    existing_id = uuid4()
    # 1. count < 500
    # 2. exact match -> None
    mock_pool.fetchval.side_effect = [10, None]
    # 3. recent memories return a very similar item
    mock_pool.fetch.return_value = [{"id": existing_id, "content": "Prefer dark mode architectural diagrams please"}]

    payload = MemoryProposeRequest(
        user_id=TEST_USER_ID,
        memory_type="preference",
        content="Prefer dark mode architectural diagrams",
    )
    res = await propose_memory(mock_pool, payload)
    assert res.stored is False
    assert res.reason == "near_duplicate"
    assert res.id == existing_id


@pytest.mark.asyncio
async def test_propose_memory_success(mock_pool):
    new_id = uuid4()
    mock_pool.fetchval.side_effect = [5, None]
    mock_pool.fetch.return_value = []
    mock_pool.fetchrow.return_value = {"id": new_id}

    payload = MemoryProposeRequest(
        user_id=TEST_USER_ID,
        memory_type="learning",
        content="Topic relates to distributed systems consensus algorithms",
    )
    res = await propose_memory(mock_pool, payload)
    assert res.stored is True
    assert res.reason == "created"
    assert res.id == new_id


@pytest.mark.asyncio
async def test_propose_memory_map_ownership_check(mock_pool):
    # Map owned by OTHER_USER_ID, not TEST_USER_ID
    mock_pool.fetchval.return_value = OTHER_USER_ID

    payload = MemoryProposeRequest(
        user_id=TEST_USER_ID,
        map_id=TEST_MAP_ID,
        memory_type="map_context",
        content="Notes about map",
    )
    with pytest.raises(PermissionError) as exc_info:
        await propose_memory(mock_pool, payload)
    assert "User does not own" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 2. HTTP Endpoint Tests
# ---------------------------------------------------------------------------


def test_http_get_memories_paginated(client, mock_pool):
    now = datetime.now(UTC)
    mock_pool.fetchval.return_value = 2
    mock_pool.fetch.return_value = [
        {
            "id": uuid4(),
            "user_id": TEST_USER_ID,
            "map_id": None,
            "memory_type": "preference",
            "content": "First memory",
            "is_active": True,
            "created_at": now,
        },
        {
            "id": uuid4(),
            "user_id": TEST_USER_ID,
            "map_id": None,
            "memory_type": "preference",
            "content": "Second memory",
            "is_active": True,
            "created_at": now,
        },
    ]

    resp = client.get("/memory?limit=10&offset=0")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2
    assert data["items"][0]["content"] == "First memory"


def test_http_get_memories_map_scoped_success(client, mock_pool):
    now = datetime.now(UTC)
    # 1. assert_map_owner -> True
    # 2. count -> 1
    mock_pool.fetchval.side_effect = [True, 1]
    mock_pool.fetch.return_value = [
        {
            "id": uuid4(),
            "user_id": TEST_USER_ID,
            "map_id": TEST_MAP_ID,
            "memory_type": "map_context",
            "content": "Map specific memory",
            "is_active": True,
            "created_at": now,
        }
    ]

    resp = client.get(f"/memory?map_id={TEST_MAP_ID}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["map_id"] == str(TEST_MAP_ID)


def test_http_get_memories_map_scoped_not_owner(client, mock_pool):
    # assert_map_owner -> False
    mock_pool.fetchval.return_value = False

    resp = client.get(f"/memory?map_id={OTHER_MAP_ID}")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Map not found."


def test_http_patch_memory_success(client, mock_pool):
    mem_id = uuid4()
    now = datetime.now(UTC)
    mock_pool.fetchrow.return_value = {
        "id": mem_id,
        "user_id": TEST_USER_ID,
        "map_id": None,
        "memory_type": "preference",
        "content": "Changed memory content",
        "is_active": False,
        "created_at": now,
    }

    resp = client.patch(
        f"/memory/{mem_id}",
        json={"content": "Changed memory content", "is_active": False},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["content"] == "Changed memory content"
    assert data["is_active"] is False


def test_http_patch_memory_not_found(client, mock_pool):
    mem_id = uuid4()
    mock_pool.fetchrow.return_value = None

    resp = client.patch(
        f"/memory/{mem_id}",
        json={"is_active": False},
    )
    assert resp.status_code == 404


def test_http_delete_memory_success(client, mock_pool):
    mem_id = uuid4()
    mock_pool.execute.return_value = "DELETE 1"

    resp = client.delete(f"/memory/{mem_id}")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True, "id": str(mem_id)}


def test_http_delete_memory_not_found(client, mock_pool):
    mem_id = uuid4()
    mock_pool.execute.return_value = "DELETE 0"

    resp = client.delete(f"/memory/{mem_id}")
    assert resp.status_code == 404


def test_http_gdpr_export(client, mock_pool):
    now = datetime.now(UTC)
    mock_pool.fetch.return_value = [
        {
            "id": uuid4(),
            "map_id": None,
            "memory_type": "pref",
            "content": "Export me",
            "is_active": True,
            "created_at": now,
        }
    ]

    resp = client.get("/memory/export")
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == str(TEST_USER_ID)
    assert data["total_memories"] == 1
    assert data["memories"][0]["content"] == "Export me"


def test_http_internal_propose_success(client, mock_pool):
    new_id = uuid4()
    mock_pool.fetchval.side_effect = [10, None]
    mock_pool.fetch.return_value = []
    mock_pool.fetchrow.return_value = {"id": new_id}

    payload = {
        "user_id": str(TEST_USER_ID),
        "memory_type": "fact",
        "content": "User is studying reinforcement learning",
    }
    resp = client.post("/internal/memory/propose", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["stored"] is True
    assert data["reason"] == "created"
    assert data["id"] == str(new_id)


def test_http_internal_propose_duplicate(client, mock_pool):
    dup_id = uuid4()
    mock_pool.fetchval.side_effect = [10, dup_id]

    payload = {
        "user_id": str(TEST_USER_ID),
        "memory_type": "fact",
        "content": "User is studying reinforcement learning",
    }
    resp = client.post("/internal/memory/propose", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["stored"] is False
    assert data["reason"] == "duplicate"
    assert data["id"] == str(dup_id)


def test_http_unauthenticated_rejected(mock_pool):
    # App with NO dependency override -> requires real Supabase auth -> 401
    app = FastAPI()
    app.state.pool = mock_pool
    app.include_router(memory_router)
    unauth_client = TestClient(app)

    resp = unauth_client.get("/memory")
    assert resp.status_code == 401


def test_http_internal_memory_auth_negative(mock_pool):
    """Assert internal memory routes reject unauthenticated requests without service key (AUD-01)."""
    app = FastAPI()
    app.state.pool = mock_pool
    app.include_router(internal_memory_router)
    unauth_client = TestClient(app)

    # 1. Unauthenticated GET /internal/memory
    resp = unauth_client.get(f"/internal/memory?user_id={TEST_USER_ID}")
    assert resp.status_code == 401

    # 2. Unauthenticated POST /internal/memory/propose
    resp = unauth_client.post(
        "/internal/memory/propose",
        json={"user_id": str(TEST_USER_ID), "content": "Test content", "memory_type": "fact"},
    )
    assert resp.status_code == 401


def test_http_internal_memory_requires_job_even_with_service_key(mock_pool, monkeypatch):
    app = FastAPI()
    app.state.pool = mock_pool
    app.include_router(internal_memory_router)
    monkeypatch.setattr(settings, "INTERNAL_SERVICE_KEY", "test-worker-key")
    client = TestClient(app)
    response = client.get(
        f"/internal/memory?user_id={TEST_USER_ID}&map_id={TEST_MAP_ID}",
        headers={"X-Internal-Service-Key": "test-worker-key"},
    )
    assert response.status_code == 422
    mock_pool.fetch.assert_not_awaited()


def test_http_internal_memory_admin_jwt_rejected(mock_pool):
    """Assert internal memory routes reject Admin JWTs and require worker service key (NEW-02, S3-DB2)."""
    app = FastAPI()
    app.state.pool = mock_pool
    app.include_router(internal_memory_router)
    client = TestClient(app)

    # Admin user passes Bearer token instead of X-Internal-Service-Key
    resp = client.get(
        f"/internal/memory?user_id={TEST_USER_ID}",
        headers={"Authorization": "Bearer admin-jwt-token"},
    )
    assert resp.status_code == 401
    assert "Valid internal service key required" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_propose_memory_kind_and_importance_preserved(mock_pool):
    """Assert propose_memory honors kind='preference' and custom importance (NEW-03, AUD-03)."""
    new_id = uuid4()
    # 1. Total count query
    # 2. Duplicate exact content check (None -> not duplicate)
    mock_pool.fetchval.side_effect = [5, None]
    # 3. Embedding candidates check (empty list)
    mock_pool.fetch.return_value = []
    # 4. Insert returns new row
    mock_pool.fetchrow.return_value = {
        "id": new_id,
        "user_id": TEST_USER_ID,
        "map_id": None,
        "kind": "preference",
        "content": "User prefers detailed node explanations",
        "importance": 0.85,
    }

    req = MemoryProposeRequest(
        user_id=TEST_USER_ID,
        kind="preference",
        content="User prefers detailed node explanations",
        importance=0.85,
    )

    res = await propose_memory(mock_pool, req)
    assert res.stored is True
    assert res.reason == "created"
    assert res.id == new_id

    # Verify SQL query received 'preference' and 0.85
    insert_call = mock_pool.fetchrow.call_args
    assert insert_call is not None
    args = insert_call[0]
    # $3 is kind, $6 is importance
    assert args[3] == "preference"
    assert args[6] == 0.85
