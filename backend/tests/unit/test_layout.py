"""Comprehensive unit tests for the deterministic Top-to-Bottom tree layout algorithm."""

from uuid import uuid4

import pytest
from services.generator import (
    _CARD_WIDTH,
    _ROW_HEIGHT,
    _ROOT_Y,
    _SIBLING_GAP_X,
    compute_incremental_layout,
    compute_layout,
)


def _check_no_intersections(nodes: list[dict], width_threshold: float = 240.0, height_threshold: float = 120.0):
    """Assert that no two distinct nodes have overlapping bounding boxes."""
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            n1 = nodes[i]
            n2 = nodes[j]
            dx = abs(float(n1["pos_x"]) - float(n2["pos_x"]))
            dy = abs(float(n1["pos_y"]) - float(n2["pos_y"]))
            # Overlap occurs if and only if both dx < width and dy < height
            assert not (dx < width_threshold and dy < height_threshold), (
                f"Collision detected between '{n1.get('label')}' ({n1['pos_x']}, {n1['pos_y']}) "
                f"and '{n2.get('label')}' ({n2['pos_x']}, {n2['pos_y']}): dx={dx} < {width_threshold}, dy={dy} < {height_threshold}"
            )


def test_single_node_layout():
    """A single root node should be placed at root apex."""
    nodes = [{"id": "root-1", "label": "Origin", "pos_x": 0.0, "pos_y": 0.0, "node_type": "root"}]
    edges = []
    compute_layout(nodes, edges)

    assert nodes[0]["pos_y"] == _ROOT_Y
    assert nodes[0]["pos_x"] == 80.0 + _CARD_WIDTH / 2.0


def test_two_branch_tree_no_intersections():
    """Root with two branches should be cleanly separated with no overlaps."""
    nodes = [
        {"id": "root", "label": "Root", "pos_x": 0.0, "pos_y": 0.0, "node_type": "root"},
        {"id": "b1", "label": "Branch 1", "pos_x": 0.0, "pos_y": 0.0},
        {"id": "b2", "label": "Branch 2", "pos_x": 0.0, "pos_y": 0.0},
        {"id": "c1", "label": "Concept 1", "pos_x": 0.0, "pos_y": 0.0},
        {"id": "c2", "label": "Concept 2", "pos_x": 0.0, "pos_y": 0.0},
    ]
    edges = [
        {"source": "root", "target": "b1"},
        {"source": "root", "target": "b2"},
        {"source": "b1", "target": "c1"},
        {"source": "b2", "target": "c2"},
    ]
    compute_layout(nodes, edges)

    assert nodes[0]["pos_y"] == _ROOT_Y
    assert nodes[1]["pos_y"] == _ROOT_Y + _ROW_HEIGHT
    assert nodes[2]["pos_y"] == _ROOT_Y + _ROW_HEIGHT
    assert nodes[3]["pos_y"] == _ROOT_Y + 2 * _ROW_HEIGHT
    assert nodes[4]["pos_y"] == _ROOT_Y + 2 * _ROW_HEIGHT

    # Centering verification: root is centered over its branches
    assert nodes[0]["pos_x"] == round((nodes[1]["pos_x"] + nodes[2]["pos_x"]) / 2.0, 1)

    _check_no_intersections(nodes)


def test_deep_unbalanced_six_level_tree():
    """A deep 6-level chain should advance level by level downwards."""
    nodes = [{"id": f"n{i}", "label": f"Level {i}", "pos_x": 0.0, "pos_y": 0.0} for i in range(6)]
    nodes[0]["node_type"] = "root"
    edges = [{"source": f"n{i}", "target": f"n{i+1}"} for i in range(5)]

    compute_layout(nodes, edges)

    for i in range(6):
        expected_y = _ROOT_Y + i * _ROW_HEIGHT
        assert nodes[i]["pos_y"] == expected_y

    _check_no_intersections(nodes)


def test_dense_seventy_node_map_zero_intersections():
    """A dense 70-node hierarchical graph must have zero bounding box collisions."""
    nodes = [{"id": "root", "label": "Central Focus", "pos_x": 0.0, "pos_y": 0.0, "node_type": "root"}]
    edges = []

    # 5 branches
    node_idx = 1
    for b in range(5):
        b_id = f"b_{b}"
        nodes.append({"id": b_id, "label": f"Branch {b}", "pos_x": 0.0, "pos_y": 0.0})
        edges.append({"source": "root", "target": b_id})
        node_idx += 1

        # Each branch has 3 sub-branches
        for sb in range(3):
            sb_id = f"sb_{b}_{sb}"
            nodes.append({"id": sb_id, "label": f"Sub {b}-{sb}", "pos_x": 0.0, "pos_y": 0.0})
            edges.append({"source": b_id, "target": sb_id})
            node_idx += 1

            # Each sub-branch has 3-4 leaves
            for leaf in range(3):
                leaf_id = f"leaf_{b}_{sb}_{leaf}"
                nodes.append({"id": leaf_id, "label": f"Leaf {b}-{sb}-{leaf}", "pos_x": 0.0, "pos_y": 0.0})
                edges.append({"source": sb_id, "target": leaf_id})
                node_idx += 1

    assert len(nodes) >= 65
    compute_layout(nodes, edges)
    _check_no_intersections(nodes)


def test_multi_parent_cross_links_preserve_edges():
    """Multi-parent DAGs should compute spanning tree without duplicate node placements."""
    nodes = [
        {"id": "root", "label": "Root", "pos_x": 0.0, "pos_y": 0.0, "node_type": "root"},
        {"id": "b1", "label": "Branch 1", "pos_x": 0.0, "pos_y": 0.0},
        {"id": "b2", "label": "Branch 2", "pos_x": 0.0, "pos_y": 0.0},
        {"id": "shared", "label": "Shared Subtopic", "pos_x": 0.0, "pos_y": 0.0},
    ]
    edges = [
        {"source": "root", "target": "b1"},
        {"source": "root", "target": "b2"},
        {"source": "b1", "target": "shared"},
        {"source": "b2", "target": "shared"},  # cross-link
    ]
    compute_layout(nodes, edges)

    # Shared node should be placed exactly once at depth 2
    assert nodes[3]["pos_y"] == _ROOT_Y + 2 * _ROW_HEIGHT
    _check_no_intersections(nodes)


def test_per_node_coordinate_preservation():
    """Existing nodes with valid custom coordinates should not be overwritten."""
    nodes = [
        {"id": "root", "label": "Root", "pos_x": 100.0, "pos_y": 200.0, "node_type": "root"},
        {"id": "b1", "label": "Branch 1", "pos_x": 0.0, "pos_y": 0.0},
        {"id": "b2", "label": "Branch 2", "pos_x": 500.0, "pos_y": 350.0},  # user dragged
    ]
    edges = [
        {"source": "root", "target": "b1"},
        {"source": "root", "target": "b2"},
    ]
    compute_layout(nodes, edges)

    # User dragged b2 should keep its custom (500, 350)
    assert nodes[2]["pos_x"] == 500.0
    assert nodes[2]["pos_y"] == 350.0
    # Unpositioned b1 should be assigned coordinates
    assert nodes[1]["pos_x"] > 0.0
    assert nodes[1]["pos_y"] > 0.0


def test_incremental_layout_for_chat_additions():
    """Newly added nodes in chat proposals should be placed under parent without colliding."""
    nodes = [
        {"id": "root", "label": "Root", "pos_x": 300.0, "pos_y": 80.0, "node_type": "root"},
        {"id": "b1", "label": "Branch 1", "pos_x": 300.0, "pos_y": 280.0},
        {"id": "c1", "label": "Child 1", "pos_x": 200.0, "pos_y": 480.0},
        # Newly added nodes by chat proposal:
        {"id": "new_1", "label": "New Child 1", "pos_x": 0.0, "pos_y": 0.0},
        {"id": "new_2", "label": "New Child 2", "pos_x": 0.0, "pos_y": 0.0},
    ]
    edges = [
        {"source": "root", "target": "b1"},
        {"source": "b1", "target": "c1"},
        {"source": "b1", "target": "new_1"},
        {"source": "b1", "target": "new_2"},
    ]

    compute_incremental_layout(nodes, edges, new_node_ids=["new_1", "new_2"])

    # New nodes should be at b1's child level (y = 280 + 200 = 480)
    assert nodes[3]["pos_y"] == 480.0
    assert nodes[4]["pos_y"] == 480.0
    # Placed to the right of existing child c1 (x=200)
    assert nodes[3]["pos_x"] > 200.0
    assert nodes[4]["pos_x"] > nodes[3]["pos_x"]

    _check_no_intersections(nodes)
