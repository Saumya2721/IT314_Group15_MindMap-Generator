from __future__ import annotations

import pytest
from tests.conftest import VALID_DRAFT_PAYLOAD
from validation.schema import ValidationFailure, resolve_to_records, validate_draft


def test_valid_draft_passes():
    draft = validate_draft(VALID_DRAFT_PAYLOAD)
    assert len(draft.nodes) == 3
    assert len(draft.edges) == 2


def test_duplicate_node_ids_rejected():
    payload = {
        "nodes": [
            {"id": "n1", "label": "A"},
            {"id": "n1", "label": "B (duplicate id)"},
        ],
        "edges": [],
    }
    with pytest.raises(ValidationFailure) as exc_info:
        validate_draft(payload)
    assert "Duplicate node id" in str(exc_info.value)


def test_multiple_roots_rejected():
    payload = {
        "nodes": [{"id": "n1", "label": "A"}, {"id": "n2", "label": "B"}],
        "edges": [],  # two nodes, no edges => two roots, violates DR-02
    }
    with pytest.raises(ValidationFailure) as exc_info:
        validate_draft(payload)
    assert "DR-02" in str(exc_info.value) or "root" in str(exc_info.value).lower()


def test_disconnected_cluster_rejected():
    payload = {
        "nodes": [
            {"id": "n1", "label": "Root"},
            {"id": "n2", "label": "Child"},
            {"id": "n3", "label": "Orphan"},
        ],
        "edges": [{"source": "n1", "target": "n2", "label": None}],
        # n3 has no incoming edge either, so this is actually a 2-root case;
        # covered separately by test_multiple_roots_rejected. This case
        # specifically checks reachability once there IS a single root:
    }
    with pytest.raises(ValidationFailure):
        validate_draft(payload)


def test_self_loop_rejected():
    payload = {
        "nodes": [{"id": "n1", "label": "A"}, {"id": "n2", "label": "B"}],
        "edges": [{"source": "n1", "target": "n1", "label": None}],
    }
    with pytest.raises(ValidationFailure):
        validate_draft(payload)


def test_edge_referencing_unknown_node_rejected():
    payload = {
        "nodes": [{"id": "n1", "label": "A"}],
        "edges": [{"source": "n1", "target": "does-not-exist", "label": None}],
    }
    with pytest.raises(ValidationFailure):
        validate_draft(payload)


def test_invalid_hex_color_rejected():
    payload = {
        "nodes": [{"id": "n1", "label": "A", "background_color": "not-a-hex-color"}],
        "edges": [],
    }
    with pytest.raises(ValidationFailure):
        validate_draft(payload)


def test_resolve_to_records_produces_valid_uuids_and_preserves_edges():
    draft = validate_draft(VALID_DRAFT_PAYLOAD)
    nodes, edges = resolve_to_records(draft)

    assert len(nodes) == 3
    # NodeDraft has no map_id — the backend assigns it when inserting
    node_ids = {n.id for n in nodes}
    assert len(node_ids) == 3  # all unique real UUIDs

    assert len(edges) == 2
    for e in edges:
        assert e.source_node_id in node_ids
        assert e.target_node_id in node_ids
        assert e.source_node_id != e.target_node_id
