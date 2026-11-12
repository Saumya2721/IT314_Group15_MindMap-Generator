"""Unit tests for Graph Editing API (S2-BE1).

Covers:
- Graph validation engine (single root, no cycles, reachability, same-map, self-loops)
- Incremental Node and Edge CRUD endpoints
- Cascade node deletion
- Cycle prevention on edge creation
- Batch graph updates and optimistic concurrency control (versioning)
- Graph validation endpoint
- Authorization boundary checks
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from core.supabase import current_user_id
from fastapi.testclient import TestClient
from main import app
from maps.graph_validator import (
    check_edge_creates_cycle,
    find_root_node_id,
    get_node_descendant_ids,
    validate_graph_structure,
)

# ------------------------------------------------------------------ Pure Logic Tests: Graph Validator


def test_validator_valid_single_root_tree():
    map_id = uuid4()
    root_id = uuid4()
    child1_id = uuid4()
    child2_id = uuid4()
    leaf_id = uuid4()

    nodes = [
        {"id": root_id, "label": "Central Concept", "map_id": map_id, "background_color": "#FFFFFF"},
        {"id": child1_id, "label": "Branch 1", "map_id": map_id},
        {"id": child2_id, "label": "Branch 2", "map_id": map_id},
        {"id": leaf_id, "label": "Detail 1.1", "map_id": map_id},
    ]
    edges = [
        {"id": uuid4(), "source_node_id": root_id, "target_node_id": child1_id, "map_id": map_id},
        {"id": uuid4(), "source_node_id": root_id, "target_node_id": child2_id, "map_id": map_id},
        {"id": uuid4(), "source_node_id": child1_id, "target_node_id": leaf_id, "map_id": map_id},
    ]

    errors = validate_graph_structure(nodes, edges, map_id=map_id)
    assert errors == []
    assert find_root_node_id(nodes, edges) == root_id


def test_validator_empty_graph():
    # An empty map (0 nodes) is valid
    assert validate_graph_structure([], []) == []


def test_validator_multiple_roots_fail():
    map_id = uuid4()
    r1 = uuid4()
    r2 = uuid4()
    c1 = uuid4()

    nodes = [
        {"id": r1, "label": "Root 1", "map_id": map_id},
        {"id": r2, "label": "Root 2", "map_id": map_id},
        {"id": c1, "label": "Child", "map_id": map_id},
    ]
    edges = [
        {"source_node_id": r1, "target_node_id": c1, "map_id": map_id},
    ]

    errors = validate_graph_structure(nodes, edges, map_id=map_id)
    assert any("Found 2 root concepts" in e or "DR-02" in e for e in errors)


def test_validator_no_root_due_to_cycle():
    map_id = uuid4()
    n1 = uuid4()
    n2 = uuid4()

    nodes = [
        {"id": n1, "label": "Node 1", "map_id": map_id},
        {"id": n2, "label": "Node 2", "map_id": map_id},
    ]
    edges = [
        {"source_node_id": n1, "target_node_id": n2, "map_id": map_id},
        {"source_node_id": n2, "target_node_id": n1, "map_id": map_id},
    ]

    errors = validate_graph_structure(nodes, edges, map_id=map_id)
    assert any("No root node found" in e for e in errors)


def test_validator_cycle_with_root_entry():
    map_id = uuid4()
    root = uuid4()
    a = uuid4()
    b = uuid4()

    nodes = [
        {"id": root, "label": "Root", "map_id": map_id},
        {"id": a, "label": "A", "map_id": map_id},
        {"id": b, "label": "B", "map_id": map_id},
    ]
    # Root -> A -> B -> A (cycle between A and B)
    edges = [
        {"source_node_id": root, "target_node_id": a, "map_id": map_id},
        {"source_node_id": a, "target_node_id": b, "map_id": map_id},
        {"source_node_id": b, "target_node_id": a, "map_id": map_id},
    ]

    errors = validate_graph_structure(nodes, edges, map_id=map_id)
    assert any("Cycle detected" in e for e in errors)


def test_validator_self_loop():
    map_id = uuid4()
    root = uuid4()

    nodes = [{"id": root, "label": "Root", "map_id": map_id}]
    edges = [{"source_node_id": root, "target_node_id": root, "map_id": map_id}]

    errors = validate_graph_structure(nodes, edges, map_id=map_id)
    assert any("Self-loop detected" in e for e in errors)


def test_validator_disconnected_islands():
    map_id = uuid4()
    root = uuid4()
    a = uuid4()
    b = uuid4()
    c = uuid4()

    nodes = [
        {"id": root, "label": "Root", "map_id": map_id},
        {"id": a, "label": "A", "map_id": map_id},
        {"id": b, "label": "B", "map_id": map_id},
        {"id": c, "label": "C", "map_id": map_id},
    ]
    # Root -> A, and B -> C is completely separate
    edges = [
        {"source_node_id": root, "target_node_id": a, "map_id": map_id},
        {"source_node_id": b, "target_node_id": c, "map_id": map_id},
    ]

    errors = validate_graph_structure(nodes, edges, map_id=map_id)
    # Multiple roots (root and b)
    assert any("root concepts" in e for e in errors)


def test_validator_same_map_mismatch():
    map_id_1 = uuid4()
    map_id_2 = uuid4()
    root = uuid4()
    alien = uuid4()

    nodes = [
        {"id": root, "label": "Root", "map_id": map_id_1},
        {"id": alien, "label": "Alien", "map_id": map_id_2},
    ]
    edges = [
        {"source_node_id": root, "target_node_id": alien, "map_id": map_id_1},
    ]

    errors = validate_graph_structure(nodes, edges, map_id=map_id_1)
    assert any(f"not target map '{map_id_1}'" in e for e in errors)


def test_validator_invalid_node_attributes():
    root = uuid4()
    nodes = [
        {
            "id": root,
            "label": "",  # Empty
            "background_color": "not-a-color",
            "width": -10,
        }
    ]
    errors = validate_graph_structure(nodes, [])
    assert any("non-empty label" in e for e in errors)
    assert any("invalid background_color" in e for e in errors)
    assert any("width must be greater than 0" in e for e in errors)


def test_check_edge_creates_cycle_helper():
    root = uuid4()
    a = uuid4()
    b = uuid4()

    edges = [
        {"source_node_id": root, "target_node_id": a},
        {"source_node_id": a, "target_node_id": b},
    ]

    # Adding b -> root would form root -> a -> b -> root
    assert check_edge_creates_cycle(edges, b, root) is True
    # Adding b -> a would form a -> b -> a
    assert check_edge_creates_cycle(edges, b, a) is True
    # Adding root -> b is a forward cross-link, no cycle
    assert check_edge_creates_cycle(edges, root, b) is False
    # Self-loop always creates cycle
    assert check_edge_creates_cycle(edges, a, a) is True


def test_get_node_descendant_ids():
    r = uuid4()
    a = uuid4()
    b = uuid4()
    c = uuid4()
    d = uuid4()

    # r -> a -> b -> c
    # r -> d
    edges = [
        {"source_node_id": r, "target_node_id": a},
        {"source_node_id": a, "target_node_id": b},
        {"source_node_id": b, "target_node_id": c},
        {"source_node_id": r, "target_node_id": d},
    ]

    desc_a = get_node_descendant_ids(edges, a)
    assert desc_a == {b, c}

    desc_r = get_node_descendant_ids(edges, r)
    assert desc_r == {a, b, c, d}

    desc_c = get_node_descendant_ids(edges, c)
    assert desc_c == set()


# ------------------------------------------------------------------ API Route Tests


@pytest.fixture
def test_client_and_user():
    user_id = uuid4()
    app.dependency_overrides[current_user_id] = lambda: user_id
    client = TestClient(app)
    yield client, user_id
    app.dependency_overrides.clear()


def test_node_crud_lifecycle(test_client_and_user):
    client, user_id = test_client_and_user
    map_id = uuid4()
    node_id = uuid4()

    mock_pool = MagicMock()
    # assert_map_owner returns True
    mock_pool.fetchval = AsyncMock(return_value=1)
    mock_pool.fetchrow = AsyncMock(
        return_value={
            "id": node_id,
            "map_id": map_id,
            "label": "Physics",
            "description": "Fundamental science",
            "pos_x": 100.0,
            "pos_y": 200.0,
            "background_color": "#123456",
            "text_color": "#FFFFFF",
            "width": 120.0,
            "height": 60.0,
            "node_type": "concept",
            "node_data": {"difficulty": "hard"},
            "created_at": "2026-11-07T10:00:00Z",
            "updated_at": "2026-11-07T10:00:00Z",
        }
    )
    mock_pool.fetch = AsyncMock(return_value=[mock_pool.fetchrow.return_value])
    mock_pool.execute = AsyncMock()

    # Create mock connection context manager
    mock_conn = MagicMock()
    mock_conn.fetchrow = mock_pool.fetchrow
    mock_conn.fetchval = AsyncMock(return_value=2)
    mock_conn.execute = AsyncMock()

    mock_tx = MagicMock()
    mock_tx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction.return_value = mock_tx

    mock_acquire = MagicMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire.return_value = mock_acquire

    app.state.pool = mock_pool

    # 1. Create Node
    create_payload = {
        "label": "Physics",
        "description": "Fundamental science",
        "pos_x": 100.0,
        "pos_y": 200.0,
        "background_color": "#123456",
        "text_color": "#FFFFFF",
        "width": 120.0,
        "height": 60.0,
        "node_type": "concept",
        "node_data": {"difficulty": "hard"},
    }
    res = client.post(f"/api/maps/{map_id}/nodes", json=create_payload)
    assert res.status_code == 201
    data = res.json()
    assert data["label"] == "Physics"
    assert data["map_version"] == 2

    # 2. Get Node
    res = client.get(f"/api/maps/{map_id}/nodes/{node_id}")
    assert res.status_code == 200
    assert res.json()["label"] == "Physics"

    # 3. List Nodes
    res = client.get(f"/api/maps/{map_id}/nodes")
    assert res.status_code == 200
    assert len(res.json()) == 1

    # 4. Patch Node
    patch_payload = {"label": "Theoretical Physics"}
    mock_conn.fetchrow.return_value = {**mock_pool.fetchrow.return_value, "label": "Theoretical Physics"}
    res = client.patch(f"/api/maps/{map_id}/nodes/{node_id}", json=patch_payload)
    assert res.status_code == 200
    assert res.json()["label"] == "Theoretical Physics"

    # 5. Delete Node (no children)
    mock_pool.fetch.return_value = []  # No edges
    res = client.delete(f"/api/maps/{map_id}/nodes/{node_id}")
    assert res.status_code == 200
    assert res.json()["deleted_count"] == 1


def test_edge_crud_and_cycle_prevention(test_client_and_user):
    client, user_id = test_client_and_user
    map_id = uuid4()
    src_id = uuid4()
    tgt_id = uuid4()
    edge_id = uuid4()

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=1)
    mock_pool.fetchrow = AsyncMock(
        return_value={
            "id": edge_id,
            "map_id": map_id,
            "source_node_id": src_id,
            "target_node_id": tgt_id,
            "label": "subset",
            "edge_data": None,
            "created_at": "2026-11-07T10:00:00Z",
        }
    )
    mock_pool.fetch = AsyncMock(return_value=[mock_pool.fetchrow.return_value])

    mock_conn = MagicMock()
    mock_conn.fetchrow = mock_pool.fetchrow
    mock_conn.fetchval = AsyncMock(return_value=3)
    mock_conn.execute = AsyncMock()

    mock_tx = MagicMock()
    mock_tx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction.return_value = mock_tx

    mock_acquire = MagicMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire.return_value = mock_acquire

    app.state.pool = mock_pool

    # Self-loop fails with 422
    res = client.post(
        f"/api/maps/{map_id}/edges",
        json={"source_node_id": str(src_id), "target_node_id": str(src_id)},
    )
    assert res.status_code == 422

    # Creating cycle: mock existing edge src -> tgt, and user tries tgt -> src
    mock_pool.fetch.return_value = [{"source_node_id": src_id, "target_node_id": tgt_id, "label": None}]
    res = client.post(
        f"/api/maps/{map_id}/edges",
        json={"source_node_id": str(tgt_id), "target_node_id": str(src_id)},
    )
    assert res.status_code == 422
    assert "cycle" in res.json()["detail"].lower()

    # Success path
    mock_pool.fetch.return_value = []
    res = client.post(
        f"/api/maps/{map_id}/edges",
        json={"source_node_id": str(src_id), "target_node_id": str(tgt_id), "label": "explains"},
    )
    assert res.status_code == 201
    assert res.json()["map_version"] == 3


def test_batch_graph_update_optimistic_conflict(test_client_and_user):
    client, user_id = test_client_and_user
    map_id = uuid4()

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=1)

    mock_conn = MagicMock()
    # Simulate version mismatch: update returns None, then fetch actual version returns 5
    mock_conn.fetchval = AsyncMock(side_effect=[None, 5])

    mock_tx = MagicMock()
    mock_tx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction.return_value = mock_tx

    mock_acquire = MagicMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire.return_value = mock_acquire

    app.state.pool = mock_pool

    # Expected version 2, but actual is 5 -> 409 Conflict
    payload = {
        "expected_version": 2,
        "create_nodes": [{"label": "New Concept"}],
    }
    res = client.post(f"/api/maps/{map_id}/graph/batch", json=payload)
    assert res.status_code == 409
    err = res.json()["detail"]
    assert err["error"] == "OptimisticLockConflict"
    assert err["expected_version"] == 2
    assert err["current_version"] == 5


def test_batch_graph_update_validation_failure_rolls_back(test_client_and_user):
    client, user_id = test_client_and_user
    map_id = uuid4()

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=1)

    mock_conn = MagicMock()
    mock_conn.fetchval = AsyncMock(return_value=3)
    mock_conn.fetchrow = AsyncMock(
        return_value={"id": uuid4(), "map_id": map_id, "label": "test", "pos_x": 0.0, "pos_y": 0.0}
    )
    mock_conn.execute = AsyncMock()

    # Return graph with cycle or multiple roots
    r1 = uuid4()
    r2 = uuid4()
    mock_conn.fetch = AsyncMock(
        side_effect=[
            [{"id": r1, "label": "R1", "map_id": map_id}, {"id": r2, "label": "R2", "map_id": map_id}],
            [],
        ]
    )

    mock_tx = MagicMock()
    mock_tx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction.return_value = mock_tx

    mock_acquire = MagicMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire.return_value = mock_acquire

    app.state.pool = mock_pool

    payload = {
        "expected_version": 2,
        "validate_graph": True,
        "create_nodes": [{"label": "R1"}, {"label": "R2"}],
    }
    res = client.post(f"/api/maps/{map_id}/graph/batch", json=payload)
    assert res.status_code == 422
    data = res.json()["detail"]
    assert data["error"] == "GraphValidationError"
    assert len(data["errors"]) > 0


def test_validate_graph_endpoint(test_client_and_user):
    client, user_id = test_client_and_user
    map_id = uuid4()

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=1)
    app.state.pool = mock_pool

    root_id = uuid4()
    child_id = uuid4()
    payload = {
        "nodes": [
            {"id": str(root_id), "label": "Root"},
            {"id": str(child_id), "label": "Child"},
        ],
        "edges": [
            {"source_node_id": str(root_id), "target_node_id": str(child_id)},
        ],
    }
    res = client.post(f"/api/maps/{map_id}/graph/validate", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["is_valid"] is True
    assert data["root_id"] == str(root_id)
    assert data["node_count"] == 2
    assert data["edge_count"] == 1
    assert data["errors"] == []


def test_create_node_with_parent_edge_route(test_client_and_user):
    client, user_id = test_client_and_user
    map_id = uuid4()
    parent_id = uuid4()
    new_node_id = uuid4()
    new_edge_id = uuid4()

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=1)
    mock_pool.fetch = AsyncMock(return_value=[])

    mock_conn = MagicMock()
    # Parent node lookup -> returns parent; then create node; then create edge
    mock_conn.fetchrow = AsyncMock(
        side_effect=[
            {"id": parent_id, "map_id": map_id, "label": "Parent"},  # get parent
            {"id": new_node_id, "map_id": map_id, "label": "Child"},  # create node
            {
                "id": new_edge_id,
                "map_id": map_id,
                "source_node_id": parent_id,
                "target_node_id": new_node_id,
                "label": "child_of",
            },  # create edge
        ]
    )
    mock_conn.fetchval = AsyncMock(return_value=4)

    mock_tx = MagicMock()
    mock_tx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction.return_value = mock_tx

    mock_acquire = MagicMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire.return_value = mock_acquire

    app.state.pool = mock_pool

    payload = {
        "label": "Child",
        "parent_node_id": str(parent_id),
        "edge_label": "child_of",
    }
    res = client.post(f"/api/maps/{map_id}/nodes", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["label"] == "Child"
    assert "edge" in data
    assert data["edge"]["source_node_id"] == str(parent_id)
    assert data["map_version"] == 4


def test_node_delete_requires_cascade_when_children_exist(test_client_and_user):
    client, user_id = test_client_and_user
    map_id = uuid4()
    parent_id = uuid4()
    child_id = uuid4()

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=1)
    mock_pool.fetchrow = AsyncMock(return_value={"id": parent_id, "map_id": map_id})
    mock_pool.fetch = AsyncMock(return_value=[{"id": uuid4(), "source_node_id": parent_id, "target_node_id": child_id}])

    app.state.pool = mock_pool

    # Deleting without cascade=true fails with 400
    res = client.delete(f"/api/maps/{map_id}/nodes/{parent_id}")
    assert res.status_code == 400
    assert "cascade=true" in res.json()["detail"]


def test_node_delete_cascade_succeeds(test_client_and_user):
    client, user_id = test_client_and_user
    map_id = uuid4()
    parent_id = uuid4()
    child_id = uuid4()

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=1)
    mock_pool.fetchrow = AsyncMock(return_value={"id": parent_id, "map_id": map_id})
    mock_pool.fetch = AsyncMock(return_value=[{"id": uuid4(), "source_node_id": parent_id, "target_node_id": child_id}])

    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock()
    mock_conn.fetchval = AsyncMock(return_value=5)

    mock_tx = MagicMock()
    mock_tx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction.return_value = mock_tx

    mock_acquire = MagicMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire.return_value = mock_acquire

    app.state.pool = mock_pool

    res = client.delete(f"/api/maps/{map_id}/nodes/{parent_id}?cascade=true")
    assert res.status_code == 200
    data = res.json()
    assert data["deleted_count"] == 2
    assert str(parent_id) in data["deleted_node_ids"]
    assert str(child_id) in data["deleted_node_ids"]


def test_edge_patch_and_delete(test_client_and_user):
    client, user_id = test_client_and_user
    map_id = uuid4()
    edge_id = uuid4()

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=1)
    mock_pool.fetchrow = AsyncMock(
        return_value={"id": edge_id, "map_id": map_id, "label": "old_label", "edge_data": None}
    )

    mock_conn = MagicMock()
    mock_conn.fetchrow = AsyncMock(
        return_value={"id": edge_id, "map_id": map_id, "label": "new_label", "edge_data": {"type": "dotted"}}
    )
    mock_conn.fetchval = AsyncMock(return_value=6)
    mock_conn.execute = AsyncMock(return_value="DELETE 1")

    mock_tx = MagicMock()
    mock_tx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction.return_value = mock_tx

    mock_acquire = MagicMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire.return_value = mock_acquire

    app.state.pool = mock_pool

    # Patch
    res = client.patch(f"/api/maps/{map_id}/edges/{edge_id}", json={"label": "new_label"})
    assert res.status_code == 200
    assert res.json()["label"] == "new_label"

    # Delete
    res = client.delete(f"/api/maps/{map_id}/edges/{edge_id}")
    assert res.status_code == 200
    assert "deleted successfully" in res.json()["message"]


def test_batch_graph_update_wholesale_full_sync(test_client_and_user):
    client, user_id = test_client_and_user
    map_id = uuid4()
    r_id = uuid4()
    c_id = uuid4()

    mock_pool = MagicMock()
    mock_pool.fetchval = AsyncMock(return_value=1)

    mock_conn = MagicMock()
    mock_conn.fetchval = AsyncMock(return_value=10)
    mock_conn.execute = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value={"id": r_id, "map_id": map_id})

    # When fetching nodes and edges after sync:
    mock_conn.fetch = AsyncMock(
        side_effect=[
            [{"id": r_id, "label": "Root", "map_id": map_id}, {"id": c_id, "label": "Child", "map_id": map_id}],
            [{"source_node_id": r_id, "target_node_id": c_id, "map_id": map_id}],
        ]
    )

    mock_tx = MagicMock()
    mock_tx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction.return_value = mock_tx

    mock_acquire = MagicMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire.return_value = mock_acquire

    app.state.pool = mock_pool

    payload = {
        "nodes": [
            {"id": str(r_id), "label": "Root"},
            {"id": str(c_id), "label": "Child"},
        ],
        "edges": [
            {"source_node_id": str(r_id), "target_node_id": str(c_id)},
        ],
        "validate_graph": True,
    }
    res = client.put(f"/api/maps/{map_id}/graph", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["version"] == 10
    assert data["nodes_count"] == 2
    assert data["edges_count"] == 1


def test_authorization_boundary_forbidden_for_other_user(test_client_and_user):
    client, user_id = test_client_and_user
    other_map_id = uuid4()

    mock_pool = MagicMock()
    # assert_map_owner returns 0 / False (not owner)
    mock_pool.fetchval = AsyncMock(return_value=0)
    app.state.pool = mock_pool

    res = client.get(f"/api/maps/{other_map_id}/nodes")
    assert res.status_code == 404
    assert res.json()["detail"] == "Map not found"
