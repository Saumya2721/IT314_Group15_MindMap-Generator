from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from contracts.models import ChunkSource
from orchestration.nodes import make_nodes
from rag.pinecone_store import RetrievedChunkRecord
from rag.retrieval import RetrievedChunk
from validation.schema import (
    _compute_tree_depth,
    resolve_to_records,
    run_quality_checks,
    validate_draft,
)

from tests.conftest import HEALTHY_10_NODE_PAYLOAD, VALID_DRAFT_PAYLOAD


def _make_chunk(
    text: str,
    *,
    chunk_id: str = "chunk-1",
    title: str | None = None,
    url: str | None = None,
) -> RetrievedChunk:
    record = RetrievedChunkRecord(
        id=chunk_id,
        chunk_text=text,
        source=ChunkSource.WEB_SEARCH,
        source_title=title,
        source_url=url,
        score=0.9,
    )
    return RetrievedChunk(record=record, relevance_score=0.9)


def test_compute_tree_depth():
    draft = validate_draft(HEALTHY_10_NODE_PAYLOAD)
    # n1 (root) -> n2, n3 (depth 1) -> n4..n10 (depth 2)
    depth = _compute_tree_depth(draft.nodes, draft.edges)
    assert depth == 2


def test_quality_checks_passes_healthy_draft():
    draft = validate_draft(HEALTHY_10_NODE_PAYLOAD)
    report = run_quality_checks(
        draft,
        target_node_range=(10, 20),
        target_max_depth=3,
        supporting_context=[],
    )
    assert report.passed is True
    assert len(report.warnings) == 0


def test_quality_checks_fails_under_target_node_count():
    # VALID_DRAFT_PAYLOAD has only 3 nodes
    draft = validate_draft(VALID_DRAFT_PAYLOAD)
    report = run_quality_checks(
        draft,
        target_node_range=(10, 25),
        target_max_depth=3,
    )
    assert report.passed is False
    assert any("below target minimum" in w for w in report.warnings)


def test_quality_checks_warns_over_target_node_count():
    draft = validate_draft(HEALTHY_10_NODE_PAYLOAD)  # 10 nodes
    report = run_quality_checks(
        draft,
        target_node_range=(5, 8),  # max 8 nodes
        target_max_depth=3,
    )
    # Exceeding max is a soft warning, passed remains True because min is met
    assert report.passed is True
    assert any("exceeds target maximum" in w for w in report.warnings)


def test_quality_checks_warns_on_excessive_depth():
    # Build a linear chain: n1 -> n2 -> n3 -> n4 -> n5 (depth 4)
    payload = {
        "nodes": [
            {"id": "n1", "label": "L1", "description": "Desc 1"},
            {"id": "n2", "label": "L2", "description": "Desc 2"},
            {"id": "n3", "label": "L3", "description": "Desc 3"},
            {"id": "n4", "label": "L4", "description": "Desc 4"},
            {"id": "n5", "label": "L5", "description": None},
        ],
        "edges": [
            {"source": "n1", "target": "n2", "label": None},
            {"source": "n2", "target": "n3", "label": None},
            {"source": "n3", "target": "n4", "label": None},
            {"source": "n4", "target": "n5", "label": None},
        ],
    }
    draft = validate_draft(payload)
    report = run_quality_checks(
        draft,
        target_node_range=(3, 10),
        target_max_depth=2,  # max depth 2, actual 4
    )
    assert any("exceeds target depth" in w for w in report.warnings)


def test_quality_checks_warns_on_missing_branch_description():
    payload = {
        "nodes": [
            {"id": "n1", "label": "Root", "description": "Root desc"},
            {"id": "n2", "label": "Branch without desc", "description": None},  # non-leaf missing desc!
            {"id": "n3", "label": "Leaf", "description": None},
        ],
        "edges": [
            {"source": "n1", "target": "n2", "label": None},
            {"source": "n2", "target": "n3", "label": None},
        ],
    }
    draft = validate_draft(payload)
    report = run_quality_checks(draft, target_node_range=(2, 5), target_max_depth=3)
    assert any("missing a required 2-3 sentence description" in w for w in report.warnings)


def test_quality_checks_warns_on_invalid_citation_index():
    payload = {
        "nodes": [
            {"id": "n1", "label": "Root", "description": "Root desc", "sources": ["ctx:99"]},
            {"id": "n2", "label": "Leaf", "description": None, "sources": ["ctx:1"]},
        ],
        "edges": [{"source": "n1", "target": "n2", "label": None}],
    }
    draft = validate_draft(payload)
    chunks = [_make_chunk("Context 1")]  # Only 1 chunk provided
    report = run_quality_checks(draft, target_node_range=(2, 5), target_max_depth=3, supporting_context=chunks)
    assert any("cites non-existent context item 'ctx:99'" in w for w in report.warnings)


def test_resolve_to_records_resolves_ctx_citations():
    payload = {
        "nodes": [
            {
                "id": "n1",
                "label": "Transformer",
                "description": "Attention mechanism",
                "sources": ["ctx:1", "ctx:2", "seed"],
            },
            {"id": "n2", "label": "Self-Attention", "description": None, "sources": ["ctx:1"]},
        ],
        "edges": [{"source": "n1", "target": "n2", "label": None}],
    }
    draft = validate_draft(payload)
    chunks = [
        _make_chunk("Attention paper", title="Attention Is All You Need", url="https://arxiv.org/abs/1706.03762"),
        _make_chunk("BERT paper", title=None, url="https://arxiv.org/abs/1810.04805"),
    ]
    nodes, edges = resolve_to_records(draft, supporting_context=chunks)
    assert len(nodes) == 2
    root_node = next(n for n in nodes if n.label == "Transformer")
    assert root_node.sources == [
        "Attention Is All You Need (https://arxiv.org/abs/1706.03762)",
        "https://arxiv.org/abs/1810.04805",
        "seed",
    ]


@pytest.mark.asyncio
async def test_validate_node_triggers_retry_when_under_target_and_retries_remain():
    mock_deps = MagicMock()
    mock_deps.settings.generation.max_generation_retries = 2
    mock_deps.settings.generation.enable_dynamic_prompts = True
    mock_deps.callback.progress = AsyncMock()

    nodes = make_nodes(mock_deps)

    # 3-node draft when target is 10-25
    state = {
        "draft_payload": VALID_DRAFT_PAYLOAD,
        "retry_count": 0,
        "_max_retries": 2,
        "target_node_range": (10, 25),
        "target_max_depth": 3,
        "retrieved_chunks": [],
    }

    res = await nodes["validate"](state)
    # Should trigger retry: increment retry_count to 1 and populate validation_errors
    assert res["retry_count"] == 1
    assert "best_valid_draft" in res
    assert len(res["validation_errors"]) > 0
    assert any("below target minimum" in err for err in res["validation_errors"])


@pytest.mark.asyncio
async def test_validate_node_accepts_draft_with_warnings_when_retries_exhausted():
    mock_deps = MagicMock()
    mock_deps.settings.generation.max_generation_retries = 2
    mock_deps.settings.generation.enable_dynamic_prompts = True
    mock_deps.callback.progress = AsyncMock()

    nodes = make_nodes(mock_deps)

    # 3-node draft but retry_count is already 2 (retries exhausted)
    state = {
        "draft_payload": VALID_DRAFT_PAYLOAD,
        "retry_count": 2,
        "_max_retries": 2,
        "target_node_range": (10, 25),
        "target_max_depth": 3,
        "retrieved_chunks": [],
    }

    res = await nodes["validate"](state)
    # Soft quality gate: accepts the draft even though quality failed!
    assert res["validation_errors"] == []
    assert len(res["nodes"]) == 3
    assert len(res["edges"]) == 2
    assert len(res["quality_warnings"]) > 0
    assert any("below target minimum" in w for w in res["quality_warnings"])
