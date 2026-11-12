"""Unit tests for server-backed dashboard search (S2-FE5 / US-08).

Covers:
- `GET /api/maps?q=` delegating to `repository.search_maps` with the
  user-scoped ILIKE pattern and pagination parameter.
- `search_maps` repo helper argument validation, pattern escaping, and
  result mapping.
- Empty/whitespace queries returning an empty result set.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import main
from core.supabase import current_user_id
from db.repository import search_maps
from fastapi.testclient import TestClient

MAP_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
UID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _search_row(**overrides) -> dict:
    base = {
        "id": MAP_ID,
        "title": "Weekly Project Review",
        "description": "Quarterly OKRs and action items",
        "is_public": False,
        "node_count": 6,
        "latest_job_status": "completed",
        "latest_job_progress": 100,
        "latest_job_id": None,
        "created_at": "2026-11-10T09:00:00Z",
        "updated_at": "2026-11-12T12:00:00Z",
    }
    base.update(overrides)
    return base


def _mock_pool() -> MagicMock:
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    mock_tx = MagicMock()
    mock_tx.__aenter__ = AsyncMock(return_value=pool)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    pool.transaction = MagicMock(return_value=mock_tx)
    mock_acquire = MagicMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=pool)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)
    pool.acquire.return_value = mock_acquire
    return pool


def _override_current_user():
    return UID


def _restore_app_state(orig_pool, orig_override):
    main.app.state.pool = orig_pool
    if orig_override is None:
        main.app.dependency_overrides.pop(current_user_id, None)
    else:
        main.app.dependency_overrides[current_user_id] = orig_override


# ------------------------------------------------------------------ Route tests


def test_list_maps_with_query_performs_case_insensitive_search():
    client = TestClient(main.app)
    mock_pool = _mock_pool()
    captured = {}

    async def capturing_fetch(sql, *params):
        captured["sql"] = sql
        captured["params"] = params
        return [dict(_search_row())]

    mock_pool.fetch = capturing_fetch
    orig_pool = getattr(main.app.state, "pool", None)
    orig_override = main.app.dependency_overrides.get(current_user_id)
    main.app.state.pool = mock_pool
    main.app.dependency_overrides[current_user_id] = _override_current_user
    try:
        resp = client.get("/api/maps", params={"q": "weekly", "limit": 10})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert data[0]["title"] == "Weekly Project Review"
        assert captured["params"][0] == UID
        assert captured["params"][1] == "%weekly%"
        assert captured["params"][2] == 10
        assert "ILIKE $2" in captured["sql"]
        assert "ORDER BY (m.title ILIKE $2) DESC, m.updated_at DESC" in captured["sql"]
    finally:
        _restore_app_state(orig_pool, orig_override)


def test_list_maps_with_query_escapes_percent_and_underscore():
    client = TestClient(main.app)
    mock_pool = _mock_pool()
    captured = {}

    async def capturing_fetch(sql, *params):
        captured["params"] = params
        return []

    mock_pool.fetch = capturing_fetch
    orig_pool = getattr(main.app.state, "pool", None)
    orig_override = main.app.dependency_overrides.get(current_user_id)
    main.app.state.pool = mock_pool
    main.app.dependency_overrides[current_user_id] = _override_current_user
    try:
        client.get("/api/maps", params={"q": "50%_bugs"})
        assert captured["params"][1] == "%50\\%\\_bugs%"
    finally:
        _restore_app_state(orig_pool, orig_override)


def test_list_maps_whitespace_query_is_treated_as_no_search():
    client = TestClient(main.app)
    mock_pool = _mock_pool()
    captured = {}

    async def capturing_fetch(sql, *params):
        captured["params"] = params
        return []

    mock_pool.fetch = capturing_fetch
    orig_pool = getattr(main.app.state, "pool", None)
    orig_override = main.app.dependency_overrides.get(current_user_id)
    main.app.state.pool = mock_pool
    main.app.dependency_overrides[current_user_id] = _override_current_user
    try:
        resp = client.get("/api/maps", params={"q": "   "})
        assert resp.status_code == 200
        assert captured["params"] == (UID,)
    finally:
        _restore_app_state(orig_pool, orig_override)


def test_list_maps_without_query_returns_all_owned_maps():
    client = TestClient(main.app)
    mock_pool = _mock_pool()
    mock_pool.fetch = AsyncMock(return_value=[_search_row(id=MAP_ID)])
    orig_pool = getattr(main.app.state, "pool", None)
    orig_override = main.app.dependency_overrides.get(current_user_id)
    main.app.state.pool = mock_pool
    main.app.dependency_overrides[current_user_id] = _override_current_user
    try:
        resp = client.get("/api/maps")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        # Full fetch with a single user_id parameter (no pattern)
        assert mock_pool.fetch.call_args[0][1:] == (UID,)
    finally:
        _restore_app_state(orig_pool, orig_override)


def test_list_maps_limit_is_clamped_by_fastapi_validation():
    client = TestClient(main.app)
    mock_pool = _mock_pool()
    mock_pool.fetch = AsyncMock(return_value=[])
    orig_pool = getattr(main.app.state, "pool", None)
    orig_override = main.app.dependency_overrides.get(current_user_id)
    main.app.state.pool = mock_pool
    main.app.dependency_overrides[current_user_id] = _override_current_user
    try:
        resp = client.get("/api/maps", params={"q": "x", "limit": 200})
        assert resp.status_code == 422
    finally:
        _restore_app_state(orig_pool, orig_override)


def test_patch_map_updates_viewport():
    client = TestClient(main.app)
    mock_pool = _mock_pool()
    mock_pool.fetchrow = AsyncMock(
        return_value={
            "id": str(MAP_ID),
            "title": "Weekly Project Review",
            "is_public": False,
            "created_at": "2026-11-10T09:00:00Z",
            "updated_at": "2026-11-12T12:00:00Z",
        }
    )
    mock_pool.fetchval = AsyncMock(return_value=True)  # assert_map_owner returns true

    orig_pool = getattr(main.app.state, "pool", None)
    orig_override = main.app.dependency_overrides.get(current_user_id)
    main.app.state.pool = mock_pool
    main.app.dependency_overrides[current_user_id] = _override_current_user
    try:
        resp = client.patch(
            f"/api/maps/{MAP_ID}",
            json={"view_x": 120.0, "view_y": -40.0, "view_zoom": 1.25},
        )
        assert resp.status_code == 200
        captured_sql = mock_pool.fetchrow.await_args.args[0]
        captured_args = mock_pool.fetchrow.await_args.args[1:]
        assert "view_x = $2" in captured_sql
        assert "view_y = $3" in captured_sql
        assert "view_zoom = $4" in captured_sql
        assert 120.0 in captured_args
        assert -40.0 in captured_args
        assert 1.25 in captured_args
    finally:
        _restore_app_state(orig_pool, orig_override)


# ------------------------------------------------------------------ Repo tests


@pytest.mark.asyncio
async def test_search_maps_repo_returns_ranked_results():
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[_search_row(), _search_row()])
    rows = await search_maps(conn, UID, "review", limit=20)
    assert len(rows) == 2
    call_args = conn.fetch.await_args
    args = call_args.args
    assert args[0].startswith("SELECT m.id, m.title")
    assert args[1] == UID
    assert args[2] == "%review%"
    assert args[3] == 20
    assert "ORDER BY (m.title ILIKE $2) DESC, m.updated_at DESC" in args[0]
    assert "WHERE m.user_id = $1" in args[0]


@pytest.mark.asyncio
async def test_search_maps_repo_empty_query_returns_empty_list():
    conn = MagicMock()
    rows = await search_maps(conn, UID, "   ", limit=20)
    assert rows == []
    conn.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_search_maps_repo_default_limit():
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    await search_maps(conn, UID, "x")
    assert conn.fetch.await_args.args[3] == 20
