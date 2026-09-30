import pytest
from pydantic import ValidationError

from mindmap_llm.schemas import MindMapOut


def _map(nodes: list[str], edges: list[tuple[str, str]]) -> dict[str, object]:
    return {
        "nodes": [{"id": n, "label": n.upper()} for n in nodes],
        "edges": [{"source": s, "target": t, "label": "contains"} for s, t in edges],
    }


def test_valid_tree_is_accepted() -> None:
    result = MindMapOut.model_validate(_map(["a", "b", "c"], [("a", "b"), ("a", "c")]))
    assert len(result.nodes) == 3


def test_single_node_map_is_valid() -> None:
    assert MindMapOut.model_validate(_map(["a"], [])).edges == []


def test_empty_map_is_rejected() -> None:
    with pytest.raises(ValidationError, match="at least one node"):
        MindMapOut.model_validate({"nodes": [], "edges": []})


def test_duplicate_node_ids_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate node ids"):
        MindMapOut.model_validate(_map(["a", "a"], []))


def test_edge_to_unknown_node_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown node"):
        MindMapOut.model_validate(_map(["a"], [("a", "ghost")]))


def test_self_loop_is_rejected() -> None:
    with pytest.raises(ValidationError, match="self-loop"):
        MindMapOut.model_validate(_map(["a", "b"], [("a", "b"), ("b", "b")]))


def test_duplicate_edge_is_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate edge"):
        MindMapOut.model_validate(_map(["a", "b"], [("a", "b"), ("a", "b")]))


def test_disconnected_node_counts_as_second_root() -> None:
    with pytest.raises(ValidationError, match="exactly one root"):
        MindMapOut.model_validate(_map(["a", "b", "c"], [("a", "b")]))


def test_cycle_without_a_root_is_rejected() -> None:
    with pytest.raises(ValidationError, match="exactly one root"):
        MindMapOut.model_validate(_map(["a", "b"], [("a", "b"), ("b", "a")]))
