from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from contracts.models import ChunkSource
from orchestration.nodes import _deduplicate_against_seed, make_nodes
from rag.pinecone_store import RetrievedChunkRecord
from rag.retrieval import RetrievedChunk


def _make_chunk(text: str, *, chunk_id: str = "chunk-1", score: float = 0.95) -> RetrievedChunk:
    record = RetrievedChunkRecord(
        id=chunk_id,
        chunk_text=text,
        source=ChunkSource.WEB_SEARCH,
        source_title=None,
        source_url=None,
        score=score,
    )
    return RetrievedChunk(record=record, relevance_score=score)


# ---------------------------------------------------------------------------
# Unit tests for _deduplicate_against_seed
# ---------------------------------------------------------------------------


def test_deduplicate_against_seed_filters_high_overlap():
    seed = "Transformers rely heavily on multi-head attention mechanisms to capture dependencies."
    # Overlap is high: "transformers rely heavily multi-head attention mechanisms capture dependencies"
    chunk = _make_chunk(
        "Transformers rely heavily on multi-head self-attention mechanisms to model relationships.",
        chunk_id="high-overlap",
    )
    surviving = _deduplicate_against_seed([chunk], seed_text=seed, threshold=0.6)
    assert len(surviving) == 0


def test_deduplicate_against_seed_keeps_novel_chunk():
    seed = "Quantum computing uses qubits and superposition to solve certain problems exponentially faster."
    chunk = _make_chunk(
        "Neural networks use backpropagation and gradient descent to update synaptic weights.",
        chunk_id="novel",
    )
    surviving = _deduplicate_against_seed([chunk], seed_text=seed, threshold=0.6)
    assert len(surviving) == 1
    assert surviving[0].record.id == "novel"


def test_deduplicate_against_seed_empty_seed():
    chunk = _make_chunk("Some text about algorithms.", chunk_id="chunk-1")
    surviving = _deduplicate_against_seed([chunk], seed_text="", threshold=0.6)
    assert len(surviving) == 1
    assert surviving[0].record.id == "chunk-1"


def test_deduplicate_against_seed_whitespace_seed():
    chunk = _make_chunk("Some text about algorithms.", chunk_id="chunk-1")
    surviving = _deduplicate_against_seed([chunk], seed_text="   \n\t  ", threshold=0.6)
    assert len(surviving) == 1


def test_deduplicate_against_seed_custom_threshold():
    seed = "Graph algorithms like Dijkstra and Bellman-Ford find shortest paths."
    chunk = _make_chunk(
        "Dijkstra algorithms find shortest paths efficiently in directed acyclic graphs.",
        chunk_id="chunk-custom",
    )
    # Words >= 3 chars in chunk: [dijkstra, algorithms, find, shortest, paths, efficiently, directed, acyclic, graphs] (9 words)
    # Matching in seed: [dijkstra, algorithms, find, shortest, paths] (5 words) -> 5/9 = ~0.555
    # At default 0.6, it should survive (0.555 < 0.6)
    surviving_default = _deduplicate_against_seed([chunk], seed_text=seed, threshold=0.6)
    assert len(surviving_default) == 1

    # At stricter threshold 0.5, it should be filtered out (0.555 >= 0.5)
    surviving_strict = _deduplicate_against_seed([chunk], seed_text=seed, threshold=0.5)
    assert len(surviving_strict) == 0


def test_deduplicate_against_seed_short_words_ignored():
    # Only short words (< 3 chars) match: 'is', 'a', 'in', 'of'
    seed = "is a in of on to at"
    chunk = _make_chunk("is a in of distributed systems consensus", chunk_id="chunk-short")
    # Content words in chunk >= 3: [distributed, systems, consensus] (3 words)
    # Seed has no words >= 3 chars, so chunk survives
    surviving = _deduplicate_against_seed([chunk], seed_text=seed, threshold=0.6)
    assert len(surviving) == 1


# ---------------------------------------------------------------------------
# Dynamic rerank_k scaling tests in retrieve_node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_node_scales_k_for_large_seed():
    mock_deps = MagicMock()
    mock_deps.settings.generation.enable_dynamic_prompts = True
    mock_deps.settings.generation.rag_rerank_top_k = 6
    mock_deps.settings.generation.rag_top_k = 20
    mock_deps.settings.generation.seed_dedup_overlap_threshold = 0.6
    mock_deps.callback.progress = AsyncMock()
    mock_deps.tools = {}

    captured_k = None

    async def mock_rerank(decision_model, query, candidates, keep_top_k):
        nonlocal captured_k
        captured_k = keep_top_k
        return candidates[:keep_top_k]

    nodes = make_nodes(mock_deps)

    # Large seed > 3000 chars
    large_seed = "Large seed content. " * 200  # 4000 chars

    state = {
        "job_id": uuid4(),
        "namespace": "test-ns",
        "seed_text": large_seed,
        "routing_decision": {},
    }

    with (
        pytest.MonkeyPatch.context() as mp,
    ):
        mp.setattr("orchestration.nodes.retrieve_context", AsyncMock(return_value=[]))
        mp.setattr("orchestration.nodes.rerank_context", mock_rerank)

        res = await nodes["retrieve"](state)
        # Expected: min(6 * 2, 16) = 12
        assert captured_k == 12
        assert res["seed_length"] == len(large_seed)
        assert isinstance(res["prompt_items_log"], list)


@pytest.mark.asyncio
async def test_retrieve_node_scales_k_for_small_seed():
    mock_deps = MagicMock()
    mock_deps.settings.generation.enable_dynamic_prompts = True
    mock_deps.settings.generation.rag_rerank_top_k = 8
    mock_deps.settings.generation.rag_top_k = 20
    mock_deps.settings.generation.seed_dedup_overlap_threshold = 0.6
    mock_deps.callback.progress = AsyncMock()
    mock_deps.tools = {}

    captured_k = None

    async def mock_rerank(decision_model, query, candidates, keep_top_k):
        nonlocal captured_k
        captured_k = keep_top_k
        return candidates[:keep_top_k]

    nodes = make_nodes(mock_deps)

    # Small seed < 300 chars
    small_seed = "Graph algorithms in computer science."

    state = {
        "job_id": uuid4(),
        "namespace": "test-ns",
        "seed_text": small_seed,
        "routing_decision": {},
    }

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("orchestration.nodes.retrieve_context", AsyncMock(return_value=[]))
        mp.setattr("orchestration.nodes.rerank_context", mock_rerank)

        res = await nodes["retrieve"](state)
        # Expected: max(8 // 2, 3) = 4
        assert captured_k == 4
        assert res["seed_length"] == len(small_seed)


@pytest.mark.asyncio
async def test_retrieve_node_caps_k_at_rag_top_k():
    mock_deps = MagicMock()
    mock_deps.settings.generation.enable_dynamic_prompts = True
    mock_deps.settings.generation.rag_rerank_top_k = 6
    mock_deps.settings.generation.rag_top_k = 10  # rag_top_k is smaller than min(6*2, 16)=12
    mock_deps.settings.generation.seed_dedup_overlap_threshold = 0.6
    mock_deps.callback.progress = AsyncMock()
    mock_deps.tools = {}

    captured_k = None

    async def mock_rerank(decision_model, query, candidates, keep_top_k):
        nonlocal captured_k
        captured_k = keep_top_k
        return candidates[:keep_top_k]

    nodes = make_nodes(mock_deps)

    large_seed = "Large seed content. " * 200

    state = {
        "job_id": uuid4(),
        "namespace": "test-ns",
        "seed_text": large_seed,
        "routing_decision": {},
    }

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("orchestration.nodes.retrieve_context", AsyncMock(return_value=[]))
        mp.setattr("orchestration.nodes.rerank_context", mock_rerank)

        await nodes["retrieve"](state)
        # min(12, 10) = 10
        assert captured_k == 10


@pytest.mark.asyncio
async def test_retrieve_node_uses_static_k_when_dynamic_prompts_disabled():
    mock_deps = MagicMock()
    mock_deps.settings.generation.enable_dynamic_prompts = False
    mock_deps.settings.generation.rag_rerank_top_k = 6
    mock_deps.settings.generation.rag_top_k = 20
    mock_deps.settings.generation.seed_dedup_overlap_threshold = 0.6
    mock_deps.callback.progress = AsyncMock()
    mock_deps.tools = {}

    captured_k = None

    async def mock_rerank(decision_model, query, candidates, keep_top_k):
        nonlocal captured_k
        captured_k = keep_top_k
        return candidates[:keep_top_k]

    nodes = make_nodes(mock_deps)

    large_seed = "Large seed content. " * 200

    state = {
        "job_id": uuid4(),
        "namespace": "test-ns",
        "seed_text": large_seed,
        "routing_decision": {},
    }

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("orchestration.nodes.retrieve_context", AsyncMock(return_value=[]))
        mp.setattr("orchestration.nodes.rerank_context", mock_rerank)

        await nodes["retrieve"](state)
        # When dynamic prompts disabled, keep_top_k stays base_rerank_k (6)
        assert captured_k == 6
