"""Integration tests for the version-history / snapshot REST endpoints (US-04 / S2-FE7)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import main
from core.supabase import current_user_id
from fastapi.testclient import TestClient

MAP_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
SNAPSHOT_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _snapshot_row(snapshot_data: dict | None = None) -> dict:
    return {
        "id": SNAPSHOT_ID,
        "map_id": MAP_ID,
        "version_number": 3,
        "trigger_source": "full_regen",
        "trigger_job_id": uuid.UUID("33333333-3333-3333-3333-333333333333"),
        "label": "Version 3",
        "snapshot_data": snapshot_data
        if snapshot_data is not None
        else {
            "nodes": [{"id": str(uuid.uuid4()), "label": "Root", "pos_x": 100.0, "pos_y": 100.0}],
            "edges": [],
        },
        "created_by": uuid.UUID("44444444-4444-4444-4444-444444444444"),
        "created_at": "2026-11-10T00:00:00Z",
    }


def _mock_pool(owner: bool = True) -> MagicMock:
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=owner)
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value="DELETE 1")
    pool.executemany = AsyncMock()
    return pool


def _override_current_user():
    return uuid.uuid4()


def test_list_snapshots_returns_snapshot_list():
    client = TestClient(main.app)
    mock_pool = _mock_pool(owner=True)
    mock_pool.fetch = AsyncMock(
        return_value=[
            _snapshot_row(),
            {
                "id": uuid.uuid4(),
                "map_id": MAP_ID,
                "version_number": 2,
                "trigger_source": "initial_generation",
                "trigger_job_id": None,
                "label": "Version 2",
                "snapshot_data": {"nodes": [], "edges": []},
                "created_by": None,
                "created_at": "2026-11-09T00:00:00Z",
            },
        ]
    )

    orig_pool = getattr(main.app.state, "pool", None)
    main.app.state.pool = mock_pool
    main.app.dependency_overrides[current_user_id] = _override_current_user
    try:
        resp = client.get(f"/api/maps/{MAP_ID}/snapshots")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["version_number"] == 3
    finally:
        main.app.dependency_overrides.pop(current_user_id, None)
        main.app.state.pool = orig_pool


def test_get_snapshot_by_id_returns_snapshot():
    client = TestClient(main.app)
    mock_pool = _mock_pool(owner=True)
    mock_pool.fetchrow = AsyncMock(return_value=_snapshot_row())

    orig_pool = getattr(main.app.state, "pool", None)
    main.app.state.pool = mock_pool
    main.app.dependency_overrides[current_user_id] = _override_current_user
    try:
        resp = client.get(f"/api/maps/{MAP_ID}/snapshots/{SNAPSHOT_ID}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(SNAPSHOT_ID)
        assert data["snapshot_data"]["nodes"][0]["label"] == "Root"
    finally:
        main.app.dependency_overrides.pop(current_user_id, None)
        main.app.state.pool = orig_pool


def test_get_snapshot_not_found_returns_404():
    client = TestClient(main.app)
    mock_pool = _mock_pool(owner=True)
    mock_pool.fetchrow = AsyncMock(return_value=None)

    orig_pool = getattr(main.app.state, "pool", None)
    main.app.state.pool = mock_pool
    main.app.dependency_overrides[current_user_id] = _override_current_user
    try:
        resp = client.get(f"/api/maps/{MAP_ID}/snapshots/{SNAPSHOT_ID}")
        assert resp.status_code == 404
    finally:
        main.app.dependency_overrides.pop(current_user_id, None)
        main.app.state.pool = orig_pool


def test_restore_snapshot_returns_restored_graph():
    client = TestClient(main.app)
    mock_pool = _mock_pool(owner=True)
    mock_pool.fetchrow = AsyncMock(return_value=_snapshot_row())
    mock_pool.execute = AsyncMock(return_value="DELETE 1")

    orig_pool = getattr(main.app.state, "pool", None)
    main.app.state.pool = mock_pool
    main.app.dependency_overrides[current_user_id] = _override_current_user
    try:
        resp = client.post(f"/api/maps/{MAP_ID}/snapshots/{SNAPSHOT_ID}/restore")
        assert resp.status_code == 200
        data = resp.json()
        assert data["snapshot_id"] == str(SNAPSHOT_ID)
        assert "nodes" in data["snapshot_data"]
        assert "edges" in data["snapshot_data"]
    finally:
        main.app.dependency_overrides.pop(current_user_id, None)
        main.app.state.pool = orig_pool


def test_delete_snapshot_returns_success():
    client = TestClient(main.app)
    mock_pool = _mock_pool(owner=True)
    mock_pool.execute = AsyncMock(return_value="DELETE 1")

    orig_pool = getattr(main.app.state, "pool", None)
    main.app.state.pool = mock_pool
    main.app.dependency_overrides[current_user_id] = _override_current_user
    try:
        resp = client.delete(f"/api/maps/{MAP_ID}/snapshots/{SNAPSHOT_ID}")
        assert resp.status_code == 200
        assert resp.json()["message"] == "Snapshot deleted successfully"
    finally:
        main.app.dependency_overrides.pop(current_user_id, None)
        main.app.state.pool = orig_pool


def test_snapshot_endpoints_require_auth_401():
    client = TestClient(main.app)
    mock_pool = _mock_pool(owner=True)

    orig_pool = getattr(main.app.state, "pool", None)
    main.app.state.pool = mock_pool
    try:
        resp = client.get(f"/api/maps/{MAP_ID}/snapshots")
        assert resp.status_code == 401
    finally:
        main.app.state.pool = orig_pool
