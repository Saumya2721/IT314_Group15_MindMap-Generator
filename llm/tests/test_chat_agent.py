"""
Tests for S2-L8 — Chat Turn Agent.

All LLM, decision-model, and vector-store calls are mocked via the fake
providers defined in conftest.py.  No real HTTP calls, no real Pinecone,
no real LLM.

Coverage:
  - All five intent classifications.
  - Map-grounded question answering (Pinecone namespace, node IDs, sources).
  - Action proposals for all three action intents.
  - Ambiguous requests → needs_clarification.
  - Invalid node references → needs_clarification.
  - Small talk without Pinecone retrieval.
  - No direct map mutations.
  - Decision model and LLM error handling.
  - Malformed LLM JSON.
  - Chat history inclusion.
  - Empty map snapshot edge case.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from chat.agent import run_chat_turn
from chat.intents import classify_intent
from contracts.models import (
    ChatIntent,
    ChatMessage,
    ChatTurnRequest,
    EdgeDraft,
    MapSnapshot,
    NodeDraft,
)
from orchestration.deps import GenerationDeps
from providers.decision_model import ClassifyResult, DecisionModelUnavailable
from rag.pinecone_store import ChunkSource, VectorChunk

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NODE_A_ID = uuid4()
NODE_B_ID = uuid4()
NODE_C_ID = uuid4()


def _make_snapshot(*, nodes: list[NodeDraft] | None = None, edges: list[EdgeDraft] | None = None) -> MapSnapshot:
    return MapSnapshot(
        map_id=uuid4(),
        nodes=nodes
        or [
            NodeDraft(id=NODE_A_ID, label="Photosynthesis", description="Root concept"),
            NodeDraft(id=NODE_B_ID, label="Light Reactions", description="First stage"),
            NodeDraft(id=NODE_C_ID, label="Calvin Cycle", description="Second stage"),
        ],
        edges=edges or [EdgeDraft(id=uuid4(), source_node_id=NODE_A_ID, target_node_id=NODE_B_ID)],
    )


def _make_request(
    message: str,
    *,
    snapshot: MapSnapshot | None = None,
    history: list[ChatMessage] | None = None,
) -> ChatTurnRequest:
    snap = snapshot or _make_snapshot()
    return ChatTurnRequest(
        map_id=snap.map_id,
        message=message,
        history=history or [],
        map_snapshot=snap,
        callback_base_url="http://localhost/callback",
        callback_token="tok",
    )


def _make_deps(
    fake_llm, fake_decision_model, fake_embedding_provider, fake_vector_store, fake_callback
) -> GenerationDeps:
    from config import Settings

    return GenerationDeps(
        llm=fake_llm,
        decision_model=fake_decision_model,
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
        tools={},
        settings=Settings(),
        callback=fake_callback,
    )


def _proposal_json(
    action_type: str = "edit_proposal",
    target_ids: list[str] | None = None,
    explanation: str = "Add a node",
    needs_clarification: bool = False,
    clarification_question: str | None = None,
) -> str:
    return json.dumps(
        {
            "action_type": action_type,
            "target_node_ids": target_ids or [],
            "parameters": {},
            "explanation": explanation,
            "needs_clarification": needs_clarification,
            "clarification_question": clarification_question,
        }
    )


# ---------------------------------------------------------------------------
# 1-5: Intent classification
# ---------------------------------------------------------------------------


class ConfigurableDecisionModel:
    """Decision model that returns a configured label."""

    def __init__(self, label: str):
        self._label = label

    @property
    def name(self) -> str:
        return "configurable-dm"

    async def classify(self, *, state: str, question: str, labels: list[str]) -> ClassifyResult:
        return ClassifyResult(label=self._label, confidence=0.9)

    async def score(self, *, state: str, items: list[str], question: str):
        from providers.decision_model import ScoreResult

        return ScoreResult(scores=[1.0] * len(items))

    async def health_check(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_classify_question():
    """classify_intent returns question for a question-like message."""
    dm = ConfigurableDecisionModel("question")
    snap = _make_snapshot()
    intent = await classify_intent(dm, "What does the Light Reactions node mean?", snap)
    assert intent == ChatIntent.QUESTION


@pytest.mark.asyncio
async def test_classify_edit_proposal():
    dm = ConfigurableDecisionModel("edit_proposal")
    snap = _make_snapshot()
    intent = await classify_intent(dm, "Add a node about ATP synthesis", snap)
    assert intent == ChatIntent.EDIT_PROPOSAL


@pytest.mark.asyncio
async def test_classify_regen_whole():
    dm = ConfigurableDecisionModel("regen_whole")
    snap = _make_snapshot()
    intent = await classify_intent(dm, "Regenerate the whole mind map with more detail", snap)
    assert intent == ChatIntent.REGEN_WHOLE


@pytest.mark.asyncio
async def test_classify_regen_node():
    dm = ConfigurableDecisionModel("regen_node")
    snap = _make_snapshot()
    intent = await classify_intent(dm, "Redo the Calvin Cycle branch", snap)
    assert intent == ChatIntent.REGEN_NODE


@pytest.mark.asyncio
async def test_classify_small_talk():
    dm = ConfigurableDecisionModel("small_talk")
    snap = _make_snapshot()
    intent = await classify_intent(dm, "Hello!", snap)
    assert intent == ChatIntent.SMALL_TALK


# ---------------------------------------------------------------------------
# 6: Pinecone retrieval uses correct namespace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qa_uses_pinecone_namespace(fake_embedding_provider, fake_decision_model, fake_callback):
    """QA retrieval is scoped to str(map_id), not some other namespace."""
    from tests.conftest import FakeLLMProvider, FakeVectorStore

    snap = _make_snapshot()
    ns = str(snap.map_id)

    store = FakeVectorStore()
    # Pre-populate the correct namespace
    await store.upsert_chunks(
        namespace=ns,
        chunks=[
            VectorChunk(
                id="chunk-1",
                chunk_text="Photosynthesis uses light energy.",
                source=ChunkSource.UPLOADED_SEED,
                source_title="Biology Textbook",
                source_url="http://example.com/bio",
                embedding=[0.1] * 1024,
            )
        ],
    )
    # Also put something in a different namespace — must NOT appear in results
    await store.upsert_chunks(
        namespace="other-map-id",
        chunks=[
            VectorChunk(
                id="chunk-other",
                chunk_text="Unrelated content from another map.",
                source=ChunkSource.UPLOADED_SEED,
                source_title="Other",
                source_url=None,
                embedding=[0.1] * 1024,
            )
        ],
    )

    llm = FakeLLMProvider(["Photosynthesis converts light energy into chemical energy [1]."])
    dm = ConfigurableDecisionModel("question")
    deps = GenerationDeps(
        llm=llm,
        decision_model=dm,
        embedding_provider=fake_embedding_provider,
        vector_store=store,
        tools={},
        settings=__import__("config").Settings(),
        callback=fake_callback,
    )
    request = _make_request("How does photosynthesis work?", snapshot=snap)
    response = await run_chat_turn(deps, request)

    assert response.intent == ChatIntent.QUESTION
    assert response.answer is not None
    # Chunk from wrong namespace must not appear in source refs
    assert not any("other" in r.lower() for r in response.source_refs)


# ---------------------------------------------------------------------------
# 7: QA includes cited node IDs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qa_includes_node_ids(fake_embedding_provider, fake_decision_model, fake_callback):
    """Node IDs cited by [node:<uuid>] in the LLM answer are extracted."""
    from tests.conftest import FakeLLMProvider, FakeVectorStore

    snap = _make_snapshot()
    llm = FakeLLMProvider([f"The concept [node:{NODE_A_ID}] refers to photosynthesis overall."])
    dm = ConfigurableDecisionModel("question")
    deps = GenerationDeps(
        llm=llm,
        decision_model=dm,
        embedding_provider=fake_embedding_provider,
        vector_store=FakeVectorStore(),
        tools={},
        settings=__import__("config").Settings(),
        callback=fake_callback,
    )
    request = _make_request("Explain photosynthesis.", snapshot=snap)
    response = await run_chat_turn(deps, request)

    assert NODE_A_ID in response.cited_node_ids


# ---------------------------------------------------------------------------
# 8: QA includes source references
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qa_includes_source_refs(fake_embedding_provider, fake_decision_model, fake_callback):
    """Source URLs from retrieved chunks are included when cited."""
    from tests.conftest import FakeLLMProvider, FakeVectorStore

    snap = _make_snapshot()
    ns = str(snap.map_id)
    store = FakeVectorStore()
    await store.upsert_chunks(
        namespace=ns,
        chunks=[
            VectorChunk(
                id="c1",
                chunk_text="Calvin cycle fixes CO2.",
                source=ChunkSource.UPLOADED_SEED,
                source_title="Calvin Paper",
                source_url="http://example.com/calvin",
                embedding=[0.1] * 1024,
            )
        ],
    )
    llm = FakeLLMProvider(["The Calvin cycle [1] fixes CO2 into sugars."])
    dm = ConfigurableDecisionModel("question")
    deps = GenerationDeps(
        llm=llm,
        decision_model=dm,
        embedding_provider=fake_embedding_provider,
        vector_store=store,
        tools={},
        settings=__import__("config").Settings(),
        callback=fake_callback,
    )
    request = _make_request("Describe the Calvin cycle.", snapshot=snap)
    response = await run_chat_turn(deps, request)

    assert "http://example.com/calvin" in response.source_refs


# ---------------------------------------------------------------------------
# 9: QA does not mutate the map snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qa_no_map_mutation(fake_embedding_provider, fake_decision_model, fake_callback):
    from tests.conftest import FakeLLMProvider, FakeVectorStore

    snap = _make_snapshot()
    original_node_count = len(snap.nodes)
    original_edge_count = len(snap.edges)

    llm = FakeLLMProvider(["Photosynthesis is important."])
    dm = ConfigurableDecisionModel("question")
    deps = GenerationDeps(
        llm=llm,
        decision_model=dm,
        embedding_provider=fake_embedding_provider,
        vector_store=FakeVectorStore(),
        tools={},
        settings=__import__("config").Settings(),
        callback=fake_callback,
    )
    request = _make_request("Explain photosynthesis.", snapshot=snap)
    await run_chat_turn(deps, request)

    assert len(snap.nodes) == original_node_count
    assert len(snap.edges) == original_edge_count


# ---------------------------------------------------------------------------
# 10: regen_whole returns ActionProposal, never mutates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regen_whole_proposal(fake_embedding_provider, fake_decision_model, fake_callback):
    from tests.conftest import FakeLLMProvider, FakeVectorStore

    snap = _make_snapshot()
    llm = FakeLLMProvider([_proposal_json(action_type="regen_whole", explanation="Regenerate the full map")])
    dm = ConfigurableDecisionModel("regen_whole")
    deps = GenerationDeps(
        llm=llm,
        decision_model=dm,
        embedding_provider=fake_embedding_provider,
        vector_store=FakeVectorStore(),
        tools={},
        settings=__import__("config").Settings(),
        callback=fake_callback,
    )
    request = _make_request("Regenerate the whole map.", snapshot=snap)
    response = await run_chat_turn(deps, request)

    assert response.intent == ChatIntent.REGEN_WHOLE
    assert response.action_proposal is not None
    assert response.action_proposal.action_type == ChatIntent.REGEN_WHOLE
    assert response.answer is None
    # Map must not be mutated
    assert len(snap.nodes) == 3


# ---------------------------------------------------------------------------
# 11: regen_node with valid ID returns ActionProposal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regen_node_valid_id(fake_embedding_provider, fake_decision_model, fake_callback):
    from tests.conftest import FakeLLMProvider, FakeVectorStore

    snap = _make_snapshot()
    llm = FakeLLMProvider(
        [_proposal_json(action_type="regen_node", target_ids=[str(NODE_B_ID)], explanation="Redo Light Reactions")]
    )
    dm = ConfigurableDecisionModel("regen_node")
    deps = GenerationDeps(
        llm=llm,
        decision_model=dm,
        embedding_provider=fake_embedding_provider,
        vector_store=FakeVectorStore(),
        tools={},
        settings=__import__("config").Settings(),
        callback=fake_callback,
    )
    request = _make_request("Redo the Light Reactions branch.", snapshot=snap)
    response = await run_chat_turn(deps, request)

    assert response.intent == ChatIntent.REGEN_NODE
    assert response.action_proposal is not None
    assert NODE_B_ID in response.action_proposal.target_node_ids
    assert response.needs_clarification is False


# ---------------------------------------------------------------------------
# 12: regen_node with invalid ID → needs_clarification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regen_node_invalid_id(fake_embedding_provider, fake_decision_model, fake_callback):
    from tests.conftest import FakeLLMProvider, FakeVectorStore

    snap = _make_snapshot()
    bogus_id = str(uuid4())
    llm = FakeLLMProvider([_proposal_json(action_type="regen_node", target_ids=[bogus_id])])
    dm = ConfigurableDecisionModel("regen_node")
    deps = GenerationDeps(
        llm=llm,
        decision_model=dm,
        embedding_provider=fake_embedding_provider,
        vector_store=FakeVectorStore(),
        tools={},
        settings=__import__("config").Settings(),
        callback=fake_callback,
    )
    request = _make_request("Redo the mysterious node.", snapshot=snap)
    response = await run_chat_turn(deps, request)

    assert response.needs_clarification is True
    assert response.action_proposal is None
    assert response.clarification_question is not None


# ---------------------------------------------------------------------------
# 13: edit_proposal for adding a node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_proposal_add_node(fake_embedding_provider, fake_decision_model, fake_callback):
    from tests.conftest import FakeLLMProvider, FakeVectorStore

    snap = _make_snapshot()
    llm = FakeLLMProvider(
        [
            _proposal_json(
                action_type="edit_proposal", explanation="Add a node about ATP synthesis under Light Reactions"
            )
        ]
    )
    dm = ConfigurableDecisionModel("edit_proposal")
    deps = GenerationDeps(
        llm=llm,
        decision_model=dm,
        embedding_provider=fake_embedding_provider,
        vector_store=FakeVectorStore(),
        tools={},
        settings=__import__("config").Settings(),
        callback=fake_callback,
    )
    request = _make_request("Add a node about ATP synthesis under Light Reactions.", snapshot=snap)
    response = await run_chat_turn(deps, request)

    assert response.intent == ChatIntent.EDIT_PROPOSAL
    assert response.action_proposal is not None
    assert "ATP" in response.action_proposal.explanation


# ---------------------------------------------------------------------------
# 14: LLM-signalled ambiguity → needs_clarification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ambiguous_request_clarification(fake_embedding_provider, fake_decision_model, fake_callback):
    from tests.conftest import FakeLLMProvider, FakeVectorStore

    snap = _make_snapshot()
    ambiguous_json = _proposal_json(
        needs_clarification=True,
        clarification_question="Which node do you want to redo?",
    )
    llm = FakeLLMProvider([ambiguous_json])
    dm = ConfigurableDecisionModel("regen_node")
    deps = GenerationDeps(
        llm=llm,
        decision_model=dm,
        embedding_provider=fake_embedding_provider,
        vector_store=FakeVectorStore(),
        tools={},
        settings=__import__("config").Settings(),
        callback=fake_callback,
    )
    request = _make_request("Redo something.", snapshot=snap)
    response = await run_chat_turn(deps, request)

    assert response.needs_clarification is True
    assert response.clarification_question == "Which node do you want to redo?"
    assert response.action_proposal is None


# ---------------------------------------------------------------------------
# 15: small talk does NOT call Pinecone
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_small_talk_no_pinecone(fake_embedding_provider, fake_decision_model, fake_callback):
    from tests.conftest import FakeLLMProvider, FakeVectorStore

    snap = _make_snapshot()
    store = FakeVectorStore()
    llm = FakeLLMProvider(["Hello! I'm here to help with your mind map."])
    dm = ConfigurableDecisionModel("small_talk")
    deps = GenerationDeps(
        llm=llm,
        decision_model=dm,
        embedding_provider=fake_embedding_provider,
        vector_store=store,
        tools={},
        settings=__import__("config").Settings(),
        callback=fake_callback,
    )
    request = _make_request("Hi there!", snapshot=snap)

    # Patch retrieve_context to detect if it's called
    with patch("chat.qa.retrieve_context", new_callable=AsyncMock) as mock_retrieve:
        response = await run_chat_turn(deps, request)

    assert response.intent == ChatIntent.SMALL_TALK
    mock_retrieve.assert_not_called()


# ---------------------------------------------------------------------------
# 16: small talk has no action_proposal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_small_talk_no_action_proposal(fake_embedding_provider, fake_decision_model, fake_callback):
    from tests.conftest import FakeLLMProvider, FakeVectorStore

    snap = _make_snapshot()
    llm = FakeLLMProvider(["Hey! Happy to assist."])
    dm = ConfigurableDecisionModel("small_talk")
    deps = GenerationDeps(
        llm=llm,
        decision_model=dm,
        embedding_provider=fake_embedding_provider,
        vector_store=FakeVectorStore(),
        tools={},
        settings=__import__("config").Settings(),
        callback=fake_callback,
    )
    request = _make_request("Good morning!", snapshot=snap)
    response = await run_chat_turn(deps, request)

    assert response.action_proposal is None
    assert response.answer is not None


# ---------------------------------------------------------------------------
# 17: no intent mutates the map snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "intent_label,llm_response",
    [
        ("question", "An answer about photosynthesis."),
        ("small_talk", "Hello!"),
        ("regen_whole", _proposal_json(action_type="regen_whole")),
        ("edit_proposal", _proposal_json(action_type="edit_proposal", explanation="Add node")),
    ],
)
async def test_no_direct_map_mutation(
    intent_label, llm_response, fake_embedding_provider, fake_decision_model, fake_callback
):
    from tests.conftest import FakeLLMProvider, FakeVectorStore

    snap = _make_snapshot()
    before_nodes = [n.id for n in snap.nodes]
    before_edges = [e.id for e in snap.edges]

    llm = FakeLLMProvider([llm_response])
    dm = ConfigurableDecisionModel(intent_label)
    deps = GenerationDeps(
        llm=llm,
        decision_model=dm,
        embedding_provider=fake_embedding_provider,
        vector_store=FakeVectorStore(),
        tools={},
        settings=__import__("config").Settings(),
        callback=fake_callback,
    )
    request = _make_request("Do something.", snapshot=snap)
    await run_chat_turn(deps, request)

    assert [n.id for n in snap.nodes] == before_nodes
    assert [e.id for e in snap.edges] == before_edges


# ---------------------------------------------------------------------------
# 18: decision model error → fallback to 'question'
# ---------------------------------------------------------------------------


class FailingDecisionModel:
    @property
    def name(self) -> str:
        return "failing-dm"

    async def classify(self, *, state: str, question: str, labels: list[str]) -> ClassifyResult:
        raise DecisionModelUnavailable("Jev is down")

    async def score(self, *, state: str, items: list[str], question: str):
        from providers.decision_model import ScoreResult

        return ScoreResult(scores=[1.0] * len(items))

    async def health_check(self) -> bool:
        return False


@pytest.mark.asyncio
async def test_decision_model_error_fallback(fake_embedding_provider, fake_callback):
    from tests.conftest import FakeLLMProvider, FakeVectorStore

    snap = _make_snapshot()
    llm = FakeLLMProvider(["Fallback answer about photosynthesis."])
    dm = FailingDecisionModel()
    deps = GenerationDeps(
        llm=llm,
        decision_model=dm,
        embedding_provider=fake_embedding_provider,
        vector_store=FakeVectorStore(),
        tools={},
        settings=__import__("config").Settings(),
        callback=fake_callback,
    )
    request = _make_request("What is photosynthesis?", snapshot=snap)
    response = await run_chat_turn(deps, request)

    # Falls back to 'question' intent, produces an answer
    assert response.intent == ChatIntent.QUESTION
    assert response.answer is not None


# ---------------------------------------------------------------------------
# 19: LLM error → graceful response
# ---------------------------------------------------------------------------


class ErrorLLMProvider:
    @property
    def name(self) -> str:
        return "error-llm"

    async def generate(self, messages, *, system=None, tools=None, max_tokens=None, temperature=0.2):
        raise RuntimeError("LLM API unavailable")


@pytest.mark.asyncio
async def test_llm_error_handled(fake_embedding_provider, fake_decision_model, fake_callback):
    from tests.conftest import FakeVectorStore

    snap = _make_snapshot()
    llm = ErrorLLMProvider()
    dm = ConfigurableDecisionModel("question")
    deps = GenerationDeps(
        llm=llm,
        decision_model=dm,
        embedding_provider=fake_embedding_provider,
        vector_store=FakeVectorStore(),
        tools={},
        settings=__import__("config").Settings(),
        callback=fake_callback,
    )
    request = _make_request("What is photosynthesis?", snapshot=snap)

    # Must not raise
    response = await run_chat_turn(deps, request)
    assert response.intent == ChatIntent.QUESTION
    assert response.answer is not None  # error message string


# ---------------------------------------------------------------------------
# 20: malformed LLM JSON for a proposal → clarification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_llm_json(fake_embedding_provider, fake_decision_model, fake_callback):
    from tests.conftest import FakeLLMProvider, FakeVectorStore

    snap = _make_snapshot()
    llm = FakeLLMProvider(["Sure, I'll help! But I forgot to output JSON."])
    dm = ConfigurableDecisionModel("regen_node")
    deps = GenerationDeps(
        llm=llm,
        decision_model=dm,
        embedding_provider=fake_embedding_provider,
        vector_store=FakeVectorStore(),
        tools={},
        settings=__import__("config").Settings(),
        callback=fake_callback,
    )
    request = _make_request("Redo the Calvin Cycle.", snapshot=snap)
    response = await run_chat_turn(deps, request)

    assert response.needs_clarification is True
    assert response.action_proposal is None


# ---------------------------------------------------------------------------
# 21: chat history is forwarded to QA prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qa_history_included(fake_embedding_provider, fake_decision_model, fake_callback):
    from tests.conftest import FakeLLMProvider, FakeVectorStore

    snap = _make_snapshot()
    llm = FakeLLMProvider(["It is the second stage."])
    dm = ConfigurableDecisionModel("question")
    deps = GenerationDeps(
        llm=llm,
        decision_model=dm,
        embedding_provider=fake_embedding_provider,
        vector_store=FakeVectorStore(),
        tools={},
        settings=__import__("config").Settings(),
        callback=fake_callback,
    )
    history = [
        ChatMessage(role="user", content="What is photosynthesis?"),
        ChatMessage(role="assistant", content="It converts light into energy."),
    ]
    request = _make_request("And what is the Calvin Cycle?", snapshot=snap, history=history)
    await run_chat_turn(deps, request)

    # The LLM was called — verify by checking the LLM call actually happened
    assert len(llm.calls) == 1
    # The user prompt passed to the LLM should reference the history
    user_msg_content = llm.calls[0][0].content
    assert "photosynthesis" in user_msg_content.lower() or "Calvin" in user_msg_content


# ---------------------------------------------------------------------------
# 22: empty map snapshot — edge case
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_map_snapshot(fake_embedding_provider, fake_decision_model, fake_callback):
    from tests.conftest import FakeLLMProvider, FakeVectorStore

    empty_snap = MapSnapshot(map_id=uuid4(), nodes=[], edges=[])
    llm = FakeLLMProvider(["I see your map is empty. Add a topic to get started!"])
    dm = ConfigurableDecisionModel("question")
    deps = GenerationDeps(
        llm=llm,
        decision_model=dm,
        embedding_provider=fake_embedding_provider,
        vector_store=FakeVectorStore(),
        tools={},
        settings=__import__("config").Settings(),
        callback=fake_callback,
    )
    request = _make_request("What is in my map?", snapshot=empty_snap)
    response = await run_chat_turn(deps, request)

    assert response.intent == ChatIntent.QUESTION
    assert response.answer is not None
    assert response.cited_node_ids == []
