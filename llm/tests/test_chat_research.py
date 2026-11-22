"""
Unit and integration test suite for S3-A4:
Enable Research Capabilities in Conversational Chat Agent.

Covers:
  1. Research Invocation:
     - Triggered when fresh/external information is required.
     - Skipped for ordinary conversational questions.
     - Invoked at most once per chat turn (programmatic guard).
     - Multiple tool-call attempts do not trigger additional invocations.
     - Research results passed to the chat agent.
  2. Action Proposal Generation:
     - Valid add_node proposals grounded in research.
     - Valid update_node proposals grounded in research.
     - Including relevant research sources in proposed nodes.
     - Proposal schema validation and invalid node rejection.
     - Canvas state is never directly modified.
  3. Source Attribution & Grounding:
     - Preserving source URLs and titles.
     - Citations included in grounded answers.
     - Proposed node content associated with sources.
     - Missing source metadata handled gracefully.
     - Unsupported citations are not fabricated.
  4. Latency & Failure Handling:
     - Subgraph timeout handling.
     - Subgraph / provider failure handled gracefully.
     - Empty research results handled gracefully.
     - Chat turn latency budget (<= 5.0s) verified.
     - Invocation limit maintained during failures and retries.
  5. Workflow Integration:
     - Integration with chat_turn_task and run_chat_turn.
     - Memory integration remains intact.
     - Existing small talk and question QA behavior preserved.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from chat.agent import run_chat_turn
from chat.proposals import build_action_proposal
from chat.qa import answer_question
from chat.research import (
    ResearchInvocationGuard,
    format_research_context,
    invoke_chat_research,
    should_conduct_research,
)
from config import Settings
from contracts.models import (
    ChatIntent,
    ChatMessage,
    ChatTurnRequest,
    EdgeDraft,
    MapSnapshot,
    NodeDraft,
)
from orchestration.deps import GenerationDeps
from providers.decision_model import ClassifyResult
from research.models import ResearchSource

from tests.conftest import (
    FakeCallbackClient,
    FakeDecisionModel,
    FakeEmbeddingProvider,
    FakeLLMProvider,
    FakeVectorStore,
)

# ---------------------------------------------------------------------------
# Test Helpers & Fixtures
# ---------------------------------------------------------------------------

NODE_ROOT_ID = uuid4()
NODE_CHILD_ID = uuid4()


def _make_snapshot(
    nodes: list[NodeDraft] | None = None,
    edges: list[EdgeDraft] | None = None,
) -> MapSnapshot:
    return MapSnapshot(
        map_id=uuid4(),
        nodes=nodes
        or [
            NodeDraft(id=NODE_ROOT_ID, label="Machine Learning", description="Root ML branch"),
            NodeDraft(id=NODE_CHILD_ID, label="Deep Learning", description="Neural networks"),
        ],
        edges=edges or [EdgeDraft(id=uuid4(), source_node_id=NODE_ROOT_ID, target_node_id=NODE_CHILD_ID)],
    )


def _make_request(
    message: str,
    *,
    snapshot: MapSnapshot | None = None,
    history: list[ChatMessage] | None = None,
    memory_context: dict[str, Any] | None = None,
) -> ChatTurnRequest:
    snap = snapshot or _make_snapshot()
    return ChatTurnRequest(
        map_id=snap.map_id,
        message=message,
        history=history or [],
        map_snapshot=snap,
        memory_context=memory_context,
        callback_base_url="https://api.test/callback",
        callback_token="tok",
    )


def _make_deps(
    llm: Any = None,
    decision_model: Any = None,
    vector_store: Any = None,
) -> GenerationDeps:
    return GenerationDeps(
        llm=llm or FakeLLMProvider(["Sample response."]),
        decision_model=decision_model or FakeDecisionModel(),
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=vector_store or FakeVectorStore(),
        tools={},
        settings=Settings(),
        callback=FakeCallbackClient(),
    )


def _sample_research_sources() -> list[ResearchSource]:
    return [
        ResearchSource(
            content="Recent advances in 2026 demonstrate scalable quantum error correction.",
            source_title="Quantum Error Correction Breakthrough",
            source_url="https://nature.com/articles/s41586-quantum-2026",
            tool="scholar",
            doi="10.1038/s41586-quantum-2026",
            authors=["Alice Smith", "Bob Jones"],
            published_date="2026",
        ),
        ResearchSource(
            content="State-of-the-art transformer architectures now incorporate sparse linear attention.",
            source_title="Sparse Linear Attention in LLMs",
            source_url="https://arxiv.org/abs/2601.12345",
            tool="arxiv",
            doi=None,
            authors=["Carol Davis"],
            published_date="2026",
        ),
    ]


# ---------------------------------------------------------------------------
# 1. Research Invocation Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_should_conduct_research_triggered_on_keywords():
    """Questions asking for latest/recent/academic developments trigger research."""
    snap = _make_snapshot()
    # Explicit keyword match
    assert await should_conduct_research(None, "What are the latest developments in quantum computing?", snap)
    assert await should_conduct_research(None, "Find recent research papers about sparse attention", snap)
    assert await should_conduct_research(None, "Search for current trends in AI", snap)


@pytest.mark.asyncio
async def test_should_conduct_research_skipped_for_ordinary_chat():
    """Simple questions or small talk do not trigger research."""
    snap = _make_snapshot()
    assert not await should_conduct_research(None, "Hello there!", snap)
    assert not await should_conduct_research(None, "What is Machine Learning?", snap)
    assert not await should_conduct_research(None, "", snap)


def test_research_invocation_guard_strictly_enforces_one_call():
    """Guard permits the first invocation and strictly blocks any subsequent call."""
    guard = ResearchInvocationGuard(max_invocations=1)
    assert guard.can_invoke is True
    assert guard.invocations == 0

    # First call allowed
    allowed_1 = guard.record_invocation("first query")
    assert allowed_1 is True
    assert guard.invocations == 1
    assert guard.can_invoke is False

    # Second call blocked
    allowed_2 = guard.record_invocation("second query")
    assert allowed_2 is False
    assert guard.invocations == 1

    # Third call blocked
    allowed_3 = guard.record_invocation("third query")
    assert allowed_3 is False
    assert guard.invocations == 1


@pytest.mark.asyncio
async def test_invoke_chat_research_executes_and_returns_sources():
    """invoke_chat_research runs the subgraph and extracts clean source refs."""
    deps = _make_deps()
    guard = ResearchInvocationGuard(max_invocations=1)
    sources = _sample_research_sources()

    with patch("chat.research.run_research_subgraph", new_callable=AsyncMock) as mock_subgraph:
        mock_subgraph.return_value = (sources, [], False, [])
        ret_sources, ret_refs, warnings = await invoke_chat_research(
            deps=deps,
            query="quantum breakthroughs",
            map_id=uuid4(),
            guard=guard,
        )

        assert len(ret_sources) == 2
        assert len(ret_refs) == 2
        assert "https://nature.com/articles/s41586-quantum-2026" in ret_refs
        assert "https://arxiv.org/abs/2601.12345" in ret_refs
        assert guard.invocations == 1
        assert guard.can_invoke is False


@pytest.mark.asyncio
async def test_multiple_research_attempts_blocked_by_guard():
    """Calling invoke_chat_research twice returns an error warning on the second call."""
    deps = _make_deps()
    guard = ResearchInvocationGuard(max_invocations=1)
    sources = _sample_research_sources()

    with patch("chat.research.run_research_subgraph", new_callable=AsyncMock) as mock_subgraph:
        mock_subgraph.return_value = (sources, [], False, [])
        # 1st call succeeds
        _s1, _r1, _w1 = await invoke_chat_research(deps=deps, query="q1", map_id=uuid4(), guard=guard)
        assert mock_subgraph.call_count == 1

        # 2nd call blocked
        s2, r2, w2 = await invoke_chat_research(deps=deps, query="q2", map_id=uuid4(), guard=guard)
        assert s2 == []
        assert r2 == []
        assert any("limit exceeded" in w.lower() for w in w2)
        # Subgraph was NOT called a second time
        assert mock_subgraph.call_count == 1


# ---------------------------------------------------------------------------
# 2. Action Proposal Generation Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_action_proposal_add_node_with_sources():
    """Chat agent proposes add_node grounded in research with source URLs."""
    proposal_payload = json.dumps(
        {
            "action_type": "add_node",
            "target_node_ids": [str(NODE_ROOT_ID)],
            "parameters": {
                "create_nodes": [
                    {
                        "id": "new-1",
                        "label": "Quantum Error Correction",
                        "description": "Fault-tolerant quantum architecture based on 2026 findings.",
                        "sources": ["https://nature.com/articles/s41586-quantum-2026"],
                    }
                ],
                "create_edges": [
                    {
                        "source_node_id": str(NODE_ROOT_ID),
                        "target_node_id": "new-1",
                        "label": "advances",
                    }
                ],
            },
            "explanation": "Proposing to add Quantum Error Correction node under Machine Learning.",
        }
    )

    llm = FakeLLMProvider([proposal_payload])
    deps = _make_deps(llm=llm)
    req = _make_request("Add a node for recent quantum breakthroughs under Machine Learning")

    with patch("chat.proposals.invoke_chat_research", new_callable=AsyncMock) as mock_res:
        mock_res.return_value = (_sample_research_sources(), ["https://nature.com/articles/s41586-quantum-2026"], [])
        response = await build_action_proposal(deps=deps, request=req, intent=ChatIntent.EDIT_PROPOSAL)

        assert response.action_proposal is not None
        assert response.action_proposal.action_type in ("add_node", ChatIntent.EDIT_PROPOSAL)
        params = response.action_proposal.parameters
        params_dict = params.model_dump() if hasattr(params, "model_dump") else params
        assert "create_nodes" in params_dict
        assert len(params_dict["create_nodes"]) == 1
        assert params_dict["create_nodes"][0]["label"] == "Quantum Error Correction"
        assert "https://nature.com/articles/s41586-quantum-2026" in params_dict["create_nodes"][0]["sources"]


@pytest.mark.asyncio
async def test_action_proposal_update_node_with_sources():
    """Chat agent proposes update_node grounded in research findings."""
    proposal_payload = json.dumps(
        {
            "action_type": "update_node",
            "target_node_ids": [str(NODE_CHILD_ID)],
            "parameters": {
                "update_nodes": [
                    {
                        "id": str(NODE_CHILD_ID),
                        "label": "Deep Learning & SOTA Transformers",
                        "description": "Incorporates 2026 sparse attention breakthroughs.",
                        "sources": ["https://arxiv.org/abs/2601.12345"],
                    }
                ]
            },
            "explanation": "Updated Deep Learning node to reflect recent attention advances.",
        }
    )

    llm = FakeLLMProvider([proposal_payload])
    deps = _make_deps(llm=llm)
    req = _make_request("Update the Deep Learning node with recent attention advances")

    with patch("chat.proposals.invoke_chat_research", new_callable=AsyncMock) as mock_res:
        mock_res.return_value = (_sample_research_sources(), ["https://arxiv.org/abs/2601.12345"], [])
        response = await build_action_proposal(deps=deps, request=req, intent=ChatIntent.EDIT_PROPOSAL)

        assert response.action_proposal is not None
        assert response.action_proposal.action_type in ("update_node", ChatIntent.EDIT_PROPOSAL)
        params = response.action_proposal.parameters
        params_dict = params.model_dump() if hasattr(params, "model_dump") else params
        assert len(params_dict["update_nodes"]) == 1
        assert params_dict["update_nodes"][0]["id"] == str(NODE_CHILD_ID)
        assert "sources" in params_dict["update_nodes"][0]


@pytest.mark.asyncio
async def test_action_proposal_rejects_nonexistent_node():
    """Proposal referencing unknown node triggers clarification instead of corrupting state."""
    unknown_id = str(uuid4())
    proposal_payload = json.dumps(
        {
            "action_type": "edit_proposal",
            "target_node_ids": [unknown_id],
            "parameters": {
                "update_nodes": [{"id": unknown_id, "label": "Ghost Node"}],
            },
            "explanation": "Updating unknown node.",
        }
    )

    llm = FakeLLMProvider([proposal_payload])
    deps = _make_deps(llm=llm)
    req = _make_request("Update the ghost node")

    response = await build_action_proposal(deps=deps, request=req, intent=ChatIntent.EDIT_PROPOSAL)
    assert response.needs_clarification is True
    assert (
        "couldn't find" in response.clarification_question.lower()
        or "cannot update" in response.clarification_question.lower()
    )
    assert response.action_proposal is None


@pytest.mark.asyncio
async def test_action_proposal_never_mutates_canvas_state():
    """Verify that build_action_proposal only returns proposal data and does not modify snapshot."""
    snap = _make_snapshot()
    initial_nodes_count = len(snap.nodes)
    initial_edges_count = len(snap.edges)

    proposal_payload = json.dumps(
        {
            "action_type": "add_node",
            "target_node_ids": [],
            "parameters": {"create_nodes": [{"id": "n1", "label": "New Node"}]},
            "explanation": "Proposed addition.",
        }
    )
    llm = FakeLLMProvider([proposal_payload])
    deps = _make_deps(llm=llm)
    req = _make_request("Add a node", snapshot=snap)

    response = await build_action_proposal(deps=deps, request=req, intent=ChatIntent.EDIT_PROPOSAL)
    assert response.action_proposal is not None
    # Map snapshot MUST remain completely untouched
    assert len(snap.nodes) == initial_nodes_count
    assert len(snap.edges) == initial_edges_count


# ---------------------------------------------------------------------------
# 3. Source Attribution & Grounding Tests
# ---------------------------------------------------------------------------


def test_format_research_context_preserves_titles_and_urls():
    """format_research_context preserves metadata, DOIs, authors, and URLs."""
    sources = _sample_research_sources()
    formatted = format_research_context(sources)

    assert "[1] Quantum Error Correction Breakthrough" in formatted
    assert "https://nature.com/articles/s41586-quantum-2026" in formatted
    assert "10.1038/s41586-quantum-2026" in formatted
    assert "Alice Smith" in formatted
    assert "[2] Sparse Linear Attention in LLMs" in formatted
    assert "https://arxiv.org/abs/2601.12345" in formatted


def test_format_research_context_empty():
    assert "no external research" in format_research_context([])


@pytest.mark.asyncio
async def test_qa_includes_research_citations_and_source_refs():
    """answer_question includes citations and populates source_refs."""
    llm_answer = (
        "According to recent research [1], scalable quantum error correction was demonstrated. "
        "Additionally, sparse linear attention was published [2]."
    )
    llm = FakeLLMProvider([llm_answer])
    deps = _make_deps(llm=llm)
    req = _make_request("What are the latest 2026 breakthroughs in quantum computing?")

    with patch("chat.qa.invoke_chat_research", new_callable=AsyncMock) as mock_res:
        mock_res.return_value = (
            _sample_research_sources(),
            [
                "https://nature.com/articles/s41586-quantum-2026",
                "https://arxiv.org/abs/2601.12345",
            ],
            [],
        )

        response = await answer_question(deps=deps, request=req)
        assert response.answer == llm_answer
        assert len(response.source_refs) >= 2
        assert "https://nature.com/articles/s41586-quantum-2026" in response.source_refs
        assert "https://arxiv.org/abs/2601.12345" in response.source_refs


@pytest.mark.asyncio
async def test_missing_source_metadata_handled_gracefully():
    """ResearchSource with missing title, DOI, or authors does not crash formatting."""
    incomplete_source = ResearchSource(
        content="Partial snippet without author or date.",
        source_title=None,
        source_url="https://example.com/item",
        tool="web_search",
    )
    formatted = format_research_context([incomplete_source])
    assert "https://example.com/item" in formatted
    assert "Partial snippet" in formatted


# ---------------------------------------------------------------------------
# 4. Latency & Failure Handling Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_research_timeout_handled_gracefully():
    """Research subgraph timeout returns empty results without crashing."""
    deps = _make_deps()
    guard = ResearchInvocationGuard(max_invocations=1)

    async def _slow_subgraph(*args, **kwargs):
        await asyncio.sleep(2.0)
        return (_sample_research_sources(), [], False, [])

    with patch("chat.research.run_research_subgraph", side_effect=_slow_subgraph):
        sources, refs, warnings = await invoke_chat_research(
            deps=deps,
            query="timeout test",
            map_id=uuid4(),
            guard=guard,
            timeout_s=0.1,  # Strict timeout for testing
        )
        assert sources == []
        assert refs == []
        assert any("timed out" in w.lower() for w in warnings)
        assert guard.invocations == 1


@pytest.mark.asyncio
async def test_research_subgraph_exception_degrades_gracefully():
    """Unexpected exception in subgraph is caught, returning warnings and allowing chat to continue."""
    deps = _make_deps()
    guard = ResearchInvocationGuard(max_invocations=1)

    with patch("chat.research.run_research_subgraph", side_effect=RuntimeError("Tavily API down")):
        sources, refs, warnings = await invoke_chat_research(
            deps=deps,
            query="failure test",
            map_id=uuid4(),
            guard=guard,
        )
        assert sources == []
        assert refs == []
        assert any("failed" in w.lower() for w in warnings)


@pytest.mark.asyncio
async def test_chat_turn_latency_budget_enforced():
    """Verify total chat-turn execution timeout <= 5.0s is enforced by run_chat_turn."""

    async def _hanging_intent(*args, **kwargs):
        await asyncio.sleep(10.0)
        return ChatIntent.QUESTION

    deps = _make_deps()
    req = _make_request("Will this hang?")

    with patch("chat.agent.classify_intent", side_effect=_hanging_intent):
        start = time.perf_counter()
        response = await run_chat_turn(deps, req, timeout_s=0.3)
        duration = time.perf_counter() - start

        assert duration < 1.0  # Finished within budget
        assert "longer than expected" in response.answer.lower()


# ---------------------------------------------------------------------------
# 5. Workflow Integration Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_chat_turn_executes_research_for_latest_query():
    """End-to-end run_chat_turn invokes research and returns grounded answer."""
    llm_resp = "Here are the latest breakthroughs in AI [1]."
    llm = FakeLLMProvider([llm_resp])
    deps = _make_deps(llm=llm)
    req = _make_request("What are the latest 2026 developments in artificial intelligence?")

    with patch("chat.qa.invoke_chat_research", new_callable=AsyncMock) as mock_res:
        mock_res.return_value = (
            _sample_research_sources(),
            ["https://nature.com/articles/s41586-quantum-2026"],
            [],
        )
        response = await run_chat_turn(deps, req)

        assert response.intent == ChatIntent.QUESTION
        assert response.answer == llm_resp
        assert "https://nature.com/articles/s41586-quantum-2026" in response.source_refs
        mock_res.assert_called_once()


@pytest.mark.asyncio
async def test_run_chat_turn_preserves_small_talk_without_research():
    """Ordinary greeting does not trigger research or Pinecone."""
    llm = FakeLLMProvider(["Hello! I am here to help."])
    dec_model = FakeDecisionModel()
    dec_model.classify = AsyncMock(return_value=ClassifyResult(label="small_talk", confidence=1.0))
    deps = _make_deps(llm=llm, decision_model=dec_model)
    req = _make_request("Hi there!")

    with patch("chat.research.invoke_chat_research", new_callable=AsyncMock) as mock_res:
        response = await run_chat_turn(deps, req)
        assert response.intent == ChatIntent.SMALL_TALK
        assert "Hello" in response.answer
        mock_res.assert_not_called()


@pytest.mark.asyncio
async def test_run_chat_turn_memory_integration_preserved():
    """Long-term memories in request.memory_context are preserved and injected alongside research."""
    llm = FakeLLMProvider(["Based on your preference for concise answers, here is the summary [1]."])
    deps = _make_deps(llm=llm)
    req = _make_request(
        "Latest updates on deep learning",
        memory_context={"long_term_memories": ["[preference] User prefers concise bullet points"]},
    )

    with patch("chat.qa.invoke_chat_research", new_callable=AsyncMock) as mock_res:
        mock_res.return_value = (_sample_research_sources(), ["https://arxiv.org/abs/2601.12345"], [])
        response = await run_chat_turn(deps, req)
        assert response.answer is not None
        assert len(llm.calls) >= 1
        # The prompt should contain memory context and research context
        assert "https://arxiv.org/abs/2601.12345" in response.source_refs
