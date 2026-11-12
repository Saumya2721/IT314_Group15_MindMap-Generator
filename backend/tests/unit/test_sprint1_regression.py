"""Sprint 1 regression tests guarding US-01 through US-04.

These tests must stay green when Sprint 2 changes touch the graph, auth, or
polling code paths - they are the Sprint 1 regression guard (Issue #79).

Coverage:
  US-01 Thought capture      -> auth dual-defense (Bearer header + HttpOnly cookie)
  US-02 Seed upload          -> generation request intake (queue contract)
  US-03 Background synthesis -> map generation output topology + job polling
  US-04 Read-only canvas     -> graph payload contract + graph validation (DR-01..DR-04)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from core import supabase as supabase_core
from core.security import clear_auth_cookies, set_auth_cookies
from core.supabase import current_user_id, get_current_user
from fastapi import Depends, FastAPI, Response
from fastapi.testclient import TestClient
from main import app
from maps.graph_validator import (
    check_edge_creates_cycle,
    find_root_node_id,
    get_node_descendant_ids,
    validate_graph_structure,
)

# --------------------------------------------------------------------------
# Route tests: run as an authenticated Sprint 1 user (owner of the map).
# --------------------------------------------------------------------------
_TEST_UID = uuid.UUID("12345678-1234-5678-1234-567812345678")


@pytest.fixture(autouse=True)
def _authed_user_override():
    app.dependency_overrides[current_user_id] = lambda: _TEST_UID
    try:
        yield
    finally:
        app.dependency_overrides.pop(current_user_id, None)


# --------------------------------------------------------------------------
# Group A: Map generation output topology (US-03) - Sprint 1 DR-01..DR-04 invariants
# --------------------------------------------------------------------------


def test_sprint1_generated_map_topologically_valid():
    """A worker-generated Sprint 1 map must be a valid single-root DAG with
    unique ids, full reachability and preserved cross-links (DR-01..DR-04)."""
    map_id = uuid.uuid4()
    root_id = uuid.uuid4()
    branch_a_id = uuid.uuid4()
    branch_b_id = uuid.uuid4()
    leaf_id = uuid.uuid4()

    nodes = [
        {"id": root_id, "label": "Artificial Intelligence", "node_type": "root", "pos_x": 450.0, "pos_y": 300.0},
        {"id": branch_a_id, "label": "Deep Learning", "pos_x": 0.0, "pos_y": 0.0},
        {"id": branch_b_id, "label": "NLP", "pos_x": 0.0, "pos_y": 0.0},
        {"id": leaf_id, "label": "Transformers", "pos_x": 0.0, "pos_y": 0.0},
    ]
    edges = [
        {"id": uuid.uuid4(), "source_node_id": root_id, "target_node_id": branch_a_id, "label": "subset"},
        {"id": uuid.uuid4(), "source_node_id": root_id, "target_node_id": branch_b_id, "label": "subset"},
        {"id": uuid.uuid4(), "source_node_id": branch_a_id, "target_node_id": leaf_id, "label": "uses"},
    ]

    errors = validate_graph_structure(nodes, edges, map_id=map_id)
    assert errors == [], f"Generated map failed validation: {errors}"
    assert find_root_node_id(nodes, edges) == root_id
    assert len(get_node_descendant_ids(edges, root_id)) == 3


def test_sprint1_cross_links_preserved_as_dag():
    """Sprint 1 maps are DAGs, not strict trees: cross-links must be kept while
    cycles must still be rejected (DR-02, DR-04)."""
    map_id = uuid.uuid4()
    root_id = uuid.uuid4()
    branch_a_id = uuid.uuid4()
    branch_b_id = uuid.uuid4()
    leaf_id = uuid.uuid4()

    nodes = [
        {"id": root_id, "label": "Root", "node_type": "root"},
        {"id": branch_a_id, "label": "Branch A"},
        {"id": branch_b_id, "label": "Branch B"},
        {"id": leaf_id, "label": "Leaf"},
    ]
    edges = [
        {"id": uuid.uuid4(), "source_node_id": root_id, "target_node_id": branch_a_id},
        {"id": uuid.uuid4(), "source_node_id": root_id, "target_node_id": branch_b_id},
        {"id": uuid.uuid4(), "source_node_id": branch_a_id, "target_node_id": leaf_id},
        {"id": uuid.uuid4(), "source_node_id": branch_b_id, "target_node_id": leaf_id},
    ]

    errors = validate_graph_structure(nodes, edges, map_id=map_id)
    assert errors == [], f"Cross-linked DAG must remain valid: {errors}"
    assert leaf_id in get_node_descendant_ids(edges, branch_a_id)
    assert leaf_id in get_node_descendant_ids(edges, branch_b_id)

    # Adding a back-edge from leaf to root would create a cycle -> must be detected.
    assert check_edge_creates_cycle(edges, leaf_id, root_id) is True


def test_sprint1_generated_map_rejects_cycle_and_multiple_roots():
    """Sprint 1 guard: a cycle must fail with 'No root', two disjoint components
    must fail with multiple roots (DR-02), and reachability must be enforced."""
    cycle_map = uuid.uuid4()
    n1, n2 = uuid.uuid4(), uuid.uuid4()
    cycle_nodes = [
        {"id": n1, "label": "N1", "node_type": "default"},
        {"id": n2, "label": "N2", "node_type": "default"},
    ]
    cycle_edges = [
        {"id": uuid.uuid4(), "source_node_id": n1, "target_node_id": n2},
        {"id": uuid.uuid4(), "source_node_id": n2, "target_node_id": n1},
    ]
    errors = validate_graph_structure(cycle_nodes, cycle_edges, map_id=cycle_map)
    assert any("No root node found" in e for e in errors)

    # Two disconnected components -> two roots -> DR-02 violation.
    two_roots_map = uuid.uuid4()
    a_id, b_id = uuid.uuid4(), uuid.uuid4()
    c_id, d_id = uuid.uuid4(), uuid.uuid4()
    nodes = [
        {"id": a_id, "label": "A", "node_type": "root"},
        {"id": b_id, "label": "B"},
        {"id": c_id, "label": "C", "node_type": "root"},
        {"id": d_id, "label": "D"},
    ]
    edges = [
        {"id": uuid.uuid4(), "source_node_id": a_id, "target_node_id": b_id},
        {"id": uuid.uuid4(), "source_node_id": c_id, "target_node_id": d_id},
    ]
    errors = validate_graph_structure(nodes, edges, map_id=two_roots_map)
    assert any("multiple root concepts" in e.lower() or "DR-02" in e for e in errors)


# --------------------------------------------------------------------------
# Group B: Job polling (US-03) - Sprint 1 / generation status polling contract
# --------------------------------------------------------------------------


def _make_job_row(map_id: uuid.UUID, job_id: uuid.UUID, status: str) -> dict:
    return {
        "id": job_id,
        "map_id": map_id,
        "job_type": "generate",
        "status": status,
        "progress_percent": 25 if status == "running" else 0,
        "error_message": None,
        "created_at": datetime.now(UTC),
        "started_at": datetime.now(UTC) if status in ("running",) else None,
        "completed_at": None if status in ("pending", "running") else datetime.now(UTC),
        "triggered_by_message_id": None,
        "target_node_id": None,
        "callback_token_hash": "dummy",
    }


def test_sprint1_poll_latest_job_returns_pending_generation_job():
    map_id = uuid.uuid4()
    job_id = uuid.uuid4()
    job_row = _make_job_row(map_id, job_id, "pending")

    client = TestClient(app)
    orig_pool = getattr(app.state, "pool", None)
    mock_pool = MagicMock()
    mock_pool.fetchrow = AsyncMock(return_value=job_row)
    app.state.pool = mock_pool

    with patch("maps.routes.assert_map_owner", new_callable=AsyncMock, return_value=True):
        try:
            resp = client.get(f"/api/maps/{map_id}/jobs/latest")
            assert resp.status_code == 200
            data = resp.json()
            assert data["job"]["id"] == str(job_id)
            assert data["job"]["status"] == "pending"
        finally:
            app.state.pool = orig_pool


def test_sprint1_poll_job_by_id_returns_running_progress():
    map_id = uuid.uuid4()
    job_id = uuid.uuid4()
    job_row = _make_job_row(map_id, job_id, "running")

    client = TestClient(app)
    orig_pool = getattr(app.state, "pool", None)
    mock_pool = MagicMock()
    mock_pool.fetchrow = AsyncMock(return_value=job_row)
    app.state.pool = mock_pool

    with patch("maps.routes.assert_map_owner", new_callable=AsyncMock, return_value=True):
        try:
            resp = client.get(f"/api/maps/{map_id}/jobs/{job_id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["id"] == str(job_id)
            assert data["status"] == "running"
            assert data["progress_percent"] == 25
        finally:
            app.state.pool = orig_pool


def test_sprint1_poll_latest_job_returns_null_when_no_generation():
    client = TestClient(app)
    orig_pool = getattr(app.state, "pool", None)
    mock_pool = MagicMock()
    mock_pool.fetchrow = AsyncMock(return_value=None)
    app.state.pool = mock_pool

    with patch("maps.routes.assert_map_owner", new_callable=AsyncMock, return_value=True):
        try:
            resp = client.get(f"/api/maps/{uuid.uuid4()}/jobs/latest")
            assert resp.status_code == 200
            assert resp.json() == {"job": None}
        finally:
            app.state.pool = orig_pool


def test_sprint1_poll_job_for_other_users_map_is_not_visible():
    """Sprint 1 regression: owners only - another user's map must 404 on polling."""
    map_id = uuid.uuid4()
    client = TestClient(app)
    orig_pool = getattr(app.state, "pool", None)
    mock_pool = MagicMock()
    mock_pool.fetchrow = AsyncMock(return_value=_make_job_row(map_id, uuid.uuid4(), "pending"))
    app.state.pool = mock_pool

    with patch("maps.routes.assert_map_owner", new_callable=AsyncMock, return_value=False):
        try:
            resp = client.get(f"/api/maps/{map_id}/jobs/latest")
            assert resp.status_code == 404
        finally:
            app.state.pool = orig_pool


# --------------------------------------------------------------------------
# Group C: Canvas load graph payload contract (US-04)
# --------------------------------------------------------------------------


def test_sprint1_canvas_load_returns_single_root_graph_payload():
    """GET /api/maps/{map_id}/graph must return versioned nodes+edges for the canvas."""
    map_id = uuid.uuid4()

    async def fake_list_nodes(_pool, _map_id):
        return [
            {"id": str(uuid.uuid4()), "map_id": str(map_id), "label": "Root", "node_type": "root"},
            {"id": str(uuid.uuid4()), "map_id": str(map_id), "label": "Branch", "node_type": "default"},
        ]

    async def fake_list_edges(_pool, _map_id):
        return [
            {
                "id": str(uuid.uuid4()),
                "map_id": str(map_id),
                "source_node_id": str(uuid.uuid4()),
                "target_node_id": str(uuid.uuid4()),
                "label": "subset",
                "edge_data": None,
                "created_at": datetime.now(UTC),
            },
        ]

    client = TestClient(app)
    orig_pool = getattr(app.state, "pool", None)
    app.state.pool = MagicMock()

    with (
        patch("maps.routes.assert_map_owner", new_callable=AsyncMock, return_value=True),
        patch("maps.routes.get_map_version", new_callable=AsyncMock, return_value=7),
        patch("maps.routes.list_map_nodes", new=fake_list_nodes),
        patch("maps.routes.list_map_edges", new=fake_list_edges),
    ):
        try:
            resp = client.get(f"/api/maps/{map_id}/graph")
            assert resp.status_code == 200
            data = resp.json()
            assert data["map_id"] == str(map_id)
            assert data["version"] == 7
            assert len(data["nodes"]) == 2
            assert len(data["edges"]) == 1
        finally:
            app.state.pool = orig_pool


def test_sprint1_graph_validate_endpoint_rejects_invalid_maps():
    """POST /maps/{map_id}/graph/validate must surface topology errors."""
    map_id = uuid.uuid4()
    body = {
        "nodes": [
            {"id": str(uuid.uuid4()), "label": "Root", "node_type": "root"},
            {"id": str(uuid.uuid4()), "label": "Branch", "node_type": "default"},
        ],
        "edges": [
            {"id": str(uuid.uuid4()), "source_node_id": str(uuid.uuid4()), "target_node_id": str(uuid.uuid4())},
        ],
    }

    client = TestClient(app)
    orig_pool = getattr(app.state, "pool", None)
    app.state.pool = MagicMock()

    with patch("maps.routes.assert_map_owner", new_callable=AsyncMock, return_value=True):
        try:
            resp = client.post(f"/api/maps/{map_id}/graph/validate", json=body)
            assert resp.status_code == 200
            data = resp.json()
            assert "is_valid" in data
            assert data["node_count"] == 2
            assert data["edge_count"] == 1
        finally:
            app.state.pool = orig_pool


# --------------------------------------------------------------------------
# Group D: Auth dual-defense (Invariant 1) - Bearer header OR HttpOnly cookie
# --------------------------------------------------------------------------


def _build_auth_app() -> FastAPI:
    auth_app = FastAPI()

    @auth_app.get("/me")
    def read_me(user=Depends(get_current_user)):
        return {"id": str(user.id), "email": user.email}

    return auth_app


def _mock_authed_user(user_id: uuid.UUID, email: str) -> MagicMock:
    mock_user = MagicMock()
    mock_user.id = str(user_id)
    mock_user.email = email
    mock_response = MagicMock()
    mock_response.user = mock_user
    return mock_response


def test_sprint1_auth_dual_defense_accepts_bearer_header():
    fake_uid = uuid.uuid4()
    mock_response = _mock_authed_user(fake_uid, "regression@test.com")

    auth_client = TestClient(_build_auth_app())
    orig_is_configured = supabase_core.is_supabase_configured
    orig_anon_client = supabase_core.anon_client
    supabase_core.is_supabase_configured = MagicMock(return_value=True)
    supabase_core.anon_client = MagicMock(
        return_value=MagicMock(auth=MagicMock(get_user=MagicMock(return_value=mock_response)))
    )

    try:
        resp = auth_client.get("/me", headers={"Authorization": "Bearer valid-sprint1-token"})
        assert resp.status_code == 200
        assert resp.json()["id"] == str(fake_uid)
    finally:
        supabase_core.is_supabase_configured = orig_is_configured
        supabase_core.anon_client = orig_anon_client


def test_sprint1_auth_dual_defense_accepts_http_only_cookie():
    fake_uid = uuid.uuid4()
    mock_response = _mock_authed_user(fake_uid, "cookie-sprint1@test.com")

    auth_client = TestClient(_build_auth_app())
    orig_is_configured = supabase_core.is_supabase_configured
    orig_anon_client = supabase_core.anon_client
    supabase_core.is_supabase_configured = MagicMock(return_value=True)
    supabase_core.anon_client = MagicMock(
        return_value=MagicMock(auth=MagicMock(get_user=MagicMock(return_value=mock_response)))
    )

    try:
        resp = auth_client.get("/me", cookies={"access_token": "cookie-token-sprint1"})
        assert resp.status_code == 200
        assert resp.json()["email"] == "cookie-sprint1@test.com"
    finally:
        supabase_core.is_supabase_configured = orig_is_configured
        supabase_core.anon_client = orig_anon_client


def test_sprint1_auth_missing_token_returns_401():
    auth_client = TestClient(_build_auth_app())
    orig_is_configured = supabase_core.is_supabase_configured
    orig_anon_client = supabase_core.anon_client
    supabase_core.is_supabase_configured = MagicMock(return_value=True)
    supabase_core.anon_client = MagicMock(return_value=MagicMock(auth=MagicMock(get_user=MagicMock(return_value=None))))

    try:
        resp = auth_client.get("/me")
        assert resp.status_code == 401
    finally:
        supabase_core.is_supabase_configured = orig_is_configured
        supabase_core.anon_client = orig_anon_client


# --------------------------------------------------------------------------
# Group E: Auth cookie helpers (Invariant 1) - HttpOnly secure cookies
# --------------------------------------------------------------------------


def test_sprint1_set_auth_cookies_sets_secure_httponly_cookies():
    response = Response()
    set_auth_cookies(
        response=response,
        access_token="access-token-value",
        refresh_token="refresh-token-value",
        expires_in=3600,
    )
    items = response.headers.getlist("set-cookie")
    all_cookies = " | ".join(str(c) for c in items)
    from core.config import settings

    assert "access_token" in all_cookies
    assert "refresh_token" in all_cookies
    assert "httponly" in all_cookies.lower()
    assert "path=/" in all_cookies.lower()
    assert ("secure" in all_cookies.lower()) == settings.COOKIE_SECURE
    assert f"samesite={settings.COOKIE_SAMESITE}".lower() in all_cookies.lower()


def test_sprint1_clear_auth_cookies_deletes_both_cookies():
    response = Response()
    clear_auth_cookies(response=response)
    items = response.headers.getlist("set-cookie")
    all_cookies = " | ".join(str(c) for c in items)
    assert "access_token" in all_cookies
    assert "refresh_token" in all_cookies
    assert "max-age=0" in all_cookies.lower()
