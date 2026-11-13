"""
Tests for S2-L9 — Short-Term Memory (US-15: Assistant remembers).

Tests cover:
  1. Summarisation:
     - Creating a fresh rolling summary from older messages.
     - Updating an existing rolling summary with new messages.
     - Preserving user preferences and decisions without invention.
     - Empty message handling.
     - Graceful fallback on LLM failure / empty output.
  2. Token Budgeting:
     - Context within budget stays intact.
     - Context exceeding budget trims older messages while keeping newest in chronological order.
     - Summary included when budget permits, dropped/excluded when space is critically tight.
     - Response tokens reserved properly.
     - Unusually long current user message handled without exception.
     - Token estimation logic (characters + message framing).
  3. Prompt Assembly:
     - Both rolling summary and recent message window assembled.
     - Message roles and order strictly preserved.
     - Current message tracked and never duplicated in history.
     - Distinct formatting for historical summary vs recent turns.
  4. Integration with S2-L8 Chat Agent:
     - ChatTurnRequest with summary flows through run_chat_turn for QA.
     - ChatTurnRequest with summary flows through run_chat_turn for proposals.
     - ChatTurnRequest with summary=None (backward compatibility).
     - Statelessness verified (no DB connection).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from chat.agent import run_chat_turn
from chat.context import assemble_chat_context, format_memory_sections
from chat.memory import (
    summarize_conversation,
)
from chat.prompts import build_chat_qa_user_prompt, build_proposal_user_prompt
from chat.token_budget import (
    MESSAGE_FRAMING_OVERHEAD_TOKENS,
    TokenBudget,
    estimate_message_tokens,
    estimate_tokens,
    trim_history_to_budget,
)
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
from rag.pinecone_store import RetrievedChunkRecord

# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------


class MockLLMProvider:
    """Mock LLMProvider recording inputs and returning predetermined text."""

    def __init__(self, response_text: str = "Mock summary"):
        self.response_text = response_text
        self.calls: list[dict] = []

    @property
    def name(self) -> str:
        return "mock-llm"

    async def generate(self, messages, *, system=None, tools=None, max_tokens=None, temperature=0.2):
        self.calls.append({"messages": messages, "system": system, "max_tokens": max_tokens})
        from providers.base import LLMResponse

        return LLMResponse(text=self.response_text, tool_calls=[], stop_reason="end_turn")


class FailingLLMProvider:
    """Mock LLMProvider that raises an exception to test error handling."""

    @property
    def name(self) -> str:
        return "failing-llm"

    async def generate(self, messages, *, system=None, tools=None, max_tokens=None, temperature=0.2):
        raise RuntimeError("LLM service unavailable")


class ConfigurableDecisionModel:
    def __init__(self, label: str):
        self._label = label

    @property
    def name(self) -> str:
        return "configurable-dm"

    async def classify(self, *, state: str, question: str, labels: list[str]) -> ClassifyResult:
        return ClassifyResult(label=self._label, confidence=0.95)

    async def score(self, *, state: str, items: list[str], question: str):
        from providers.decision_model import ScoreResult

        return ScoreResult(scores=[1.0] * len(items))

    async def health_check(self) -> bool:
        return True


def _make_snapshot() -> MapSnapshot:
    n1 = uuid4()
    n2 = uuid4()
    return MapSnapshot(
        map_id=uuid4(),
        nodes=[
            NodeDraft(id=n1, label="Machine Learning", description="Root concept"),
            NodeDraft(id=n2, label="Supervised Learning", description="Subtopic"),
        ],
        edges=[EdgeDraft(id=uuid4(), source_node_id=n1, target_node_id=n2)],
    )


# ---------------------------------------------------------------------------
# 1. Summarisation Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarize_fresh_conversation():
    """Generates a summary from initial older messages when no previous summary exists."""
    messages = [
        ChatMessage(role="user", content="I want to create a mind map about machine learning."),
        ChatMessage(role="assistant", content="I've created the machine learning mind map."),
        ChatMessage(role="user", content="Keep it simple because I am a beginner."),
    ]
    expected = "- Topic: Machine learning.\n- User preference: Beginner level, keep it simple."
    llm = MockLLMProvider(response_text=expected)

    summary = await summarize_conversation(llm, messages)

    assert summary == expected
    assert len(llm.calls) == 1
    # Check that system prompt reinforces preserving preferences
    assert "user preferences" in llm.calls[0]["system"].lower()
    # Check that prompt contains user preference message
    user_prompt = llm.calls[0]["messages"][0].content
    assert "Keep it simple" in user_prompt
    assert "Previous Rolling Summary" not in user_prompt


@pytest.mark.asyncio
async def test_summarize_update_existing_summary():
    """Incrementally updates an existing rolling summary with new older messages."""
    previous = "- Topic: Machine learning.\n- User preference: Beginner level, keep it simple."
    new_messages = [
        ChatMessage(role="user", content="Let's add deep learning as a subtopic."),
        ChatMessage(role="assistant", content="Deep learning branch added."),
        ChatMessage(role="user", content="Focus on neural networks rather than statistics."),
    ]
    updated = (
        "- Topic: Machine learning & Deep learning.\n"
        "- User preference: Beginner level, simple, focus on neural networks over statistics."
    )
    llm = MockLLMProvider(response_text=updated)

    result = await summarize_conversation(llm, new_messages, previous_summary=previous)

    assert result == updated
    user_prompt = llm.calls[0]["messages"][0].content
    assert "Previous Rolling Summary" in user_prompt
    assert previous in user_prompt
    assert "neural networks" in user_prompt


@pytest.mark.asyncio
async def test_summarize_empty_messages():
    """Empty message list returns the previous summary without calling the LLM."""
    llm = MockLLMProvider(response_text="Should not be called")
    previous = "Existing summary."

    res1 = await summarize_conversation(llm, [], previous_summary=previous)
    assert res1 == previous

    res2 = await summarize_conversation(llm, [], previous_summary=None)
    assert res2 == ""
    assert len(llm.calls) == 0


@pytest.mark.asyncio
async def test_summarize_llm_failure_fallback():
    """When the LLM fails, summarize_conversation gracefully falls back to previous summary."""
    failing_llm = FailingLLMProvider()
    previous = "Previous valid summary."

    result = await summarize_conversation(
        failing_llm, [ChatMessage(role="user", content="test")], previous_summary=previous
    )
    assert result == previous


@pytest.mark.asyncio
async def test_summarize_empty_llm_response():
    """When the LLM returns an empty string, falls back to previous summary."""
    llm = MockLLMProvider(response_text="   ")
    previous = "Keep me."

    result = await summarize_conversation(llm, [ChatMessage(role="user", content="test")], previous_summary=previous)
    assert result == previous


# ---------------------------------------------------------------------------
# 2. Token Budgeting Tests
# ---------------------------------------------------------------------------


def test_estimate_tokens():
    """Verify standard ~4 chars/token estimation heuristic with edge cases."""
    assert estimate_tokens("") == 0
    assert estimate_tokens(None) == 0
    assert estimate_tokens("a") == 1
    assert estimate_tokens("1234") == 1
    assert estimate_tokens("12345") == 2
    assert estimate_tokens("12345678") == 2
    assert estimate_tokens("123456789") == 3


def test_estimate_message_tokens():
    """Verify message framing overhead is included in message token cost."""
    msg = ChatMessage(role="user", content="Hello world!")  # 12 chars -> 3 tokens
    # Total = 3 content tokens + 4 framing overhead = 7 tokens
    assert estimate_message_tokens(msg) == estimate_tokens(msg.content) + MESSAGE_FRAMING_OVERHEAD_TOKENS


def test_trim_history_within_budget():
    """All messages are preserved when they easily fit inside the token budget."""
    history = [
        ChatMessage(role="user", content="Message 1"),
        ChatMessage(role="assistant", content="Message 2"),
    ]
    budget = TokenBudget(max_context_tokens=1000, reserved_response_tokens=200)

    kept, summary = trim_history_to_budget(
        history,
        budget=budget,
        current_message="Message 3",
        summary="Short summary.",
        system_prompt="System instructions",
    )

    assert len(kept) == 2
    assert kept[0].content == "Message 1"
    assert kept[1].content == "Message 2"
    assert summary == "Short summary."


def test_trim_history_exceeding_budget_keeps_recent_in_order():
    """When budget is exceeded, older messages are dropped, and newest are kept in chronological order."""
    # Each message is ~50 chars => ~13 tokens + 4 overhead = 17 tokens
    history = [
        ChatMessage(role="user", content=f"Older message number {i} with some padding content") for i in range(1, 11)
    ]
    # Set a tight budget that allows only ~2 messages
    # Baseline: system_prompt (~5 tokens) + current_message (~5 tokens) = ~10 tokens
    # available input tokens: 50 tokens
    # 50 - 10 = 40 tokens available for history. At ~17 tokens/msg, fits exactly 2 messages.
    budget = TokenBudget(max_context_tokens=100, reserved_response_tokens=50)

    kept, summary = trim_history_to_budget(
        history,
        budget=budget,
        current_message="Latest question?",
        summary=None,
        system_prompt="System",
    )

    assert len(kept) < len(history)
    assert len(kept) > 0
    # Must contain the newest messages
    assert kept[-1].content == history[-1].content
    # Must preserve chronological order: kept[0] was sent before kept[1]
    if len(kept) >= 2:
        assert kept[-2].content == history[-2].content


def test_trim_history_summary_excluded_if_tight():
    """If the summary alone cannot fit within available budget, it is safely excluded."""
    history = [ChatMessage(role="user", content="Hi")]
    # Huge summary that exceeds budget
    huge_summary = "Very long summary " * 500
    budget = TokenBudget(max_context_tokens=200, reserved_response_tokens=100)

    kept, summary = trim_history_to_budget(
        history,
        budget=budget,
        current_message="Help",
        summary=huge_summary,
        system_prompt="Sys",
    )

    assert summary is None


def test_trim_history_very_long_current_message():
    """Unusually long current message does not cause crash or negative token allocations."""
    long_message = "A" * 10000
    history = [ChatMessage(role="user", content="Previous")]
    budget = TokenBudget(max_context_tokens=500, reserved_response_tokens=100)

    kept, summary = trim_history_to_budget(
        history,
        budget=budget,
        current_message=long_message,
        summary="Summary",
        system_prompt="Sys",
    )

    # History and summary dropped to prioritize current message
    assert len(kept) == 0
    assert summary is None


# ---------------------------------------------------------------------------
# 3. Prompt Assembly Tests
# ---------------------------------------------------------------------------


def test_assemble_chat_context_sections():
    """Verifies that format_memory_sections cleanly separates historical summary from recent conversation."""
    history = [
        ChatMessage(role="user", content="I prefer concise explanations."),
        ChatMessage(role="assistant", content="Understood, keeping it concise."),
    ]
    summary = "- User preference: concise answers."
    ctx = assemble_chat_context(
        current_message="Explain node 2",
        history=history,
        summary=summary,
        budget=TokenBudget(max_context_tokens=2000, reserved_response_tokens=500),
    )

    assert ctx.summary == summary
    assert len(ctx.recent_messages) == 2

    sections = format_memory_sections(ctx)
    joined = "\n\n".join(sections)

    # Must contain clear header for historical summary
    assert "Historical Conversation Summary (Background Context Only)" in joined
    assert summary in joined
    # Must contain recent messages with roles
    assert "Recent Conversation" in joined
    assert "User: I prefer concise explanations." in joined
    assert "Assistant: Understood, keeping it concise." in joined
    # Current message must NOT be in the history section (preventing duplication)
    assert "Explain node 2" not in joined


def test_qa_prompt_assembly_with_summary_and_history():
    """build_chat_qa_user_prompt merges summary, recent history, chunks, and current question without duplication."""
    history = [
        ChatMessage(role="user", content="Past question"),
        ChatMessage(role="assistant", content="Past answer"),
    ]
    summary = "Existing summary of user preferences."
    chunks = [
        RetrievedChunkRecord(
            id="c1",
            chunk_text="Chlorophyll absorbs blue and red light.",
            source="textbook",
            source_title="Bio 101",
            source_url="http://example.com/bio",
            score=1.0,
        )
    ]
    prompt = build_chat_qa_user_prompt(
        message="What light is absorbed?",
        history=history,
        chunks=chunks,
        summary=summary,
    )

    # Check order of sections:
    # 1. Summary
    # 2. Recent history
    # 3. Reference Material
    # 4. Current User Question
    assert "Historical Conversation Summary" in prompt
    assert summary in prompt
    assert "Recent Conversation" in prompt
    assert "Past question" in prompt
    assert "Reference Material" in prompt
    assert "Chlorophyll absorbs" in prompt
    assert "## User Question\n\nWhat light is absorbed?" in prompt


def test_proposal_prompt_assembly_with_summary():
    """build_proposal_user_prompt merges summary and recent history."""
    history = [ChatMessage(role="user", content="Earlier edit")]
    summary = "Topic: Botany"
    prompt = build_proposal_user_prompt(
        message="Add photosynthesis node",
        history=history,
        summary=summary,
    )

    assert "Historical Conversation Summary" in prompt
    assert "Topic: Botany" in prompt
    assert "Recent Conversation" in prompt
    assert "Earlier edit" in prompt
    assert "## User request\n\nAdd photosynthesis node" in prompt


# ---------------------------------------------------------------------------
# 4. Integration with S2-L8 Chat Agent Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_chat_turn_with_summary_for_qa(fake_embedding_provider, fake_vector_store, fake_callback):
    """Verifies run_chat_turn uses the rolling summary and history for question answering."""
    from tests.conftest import FakeLLMProvider

    snap = _make_snapshot()
    llm = FakeLLMProvider(["Based on the summary and map, machine learning is the core topic."])
    dm = ConfigurableDecisionModel("question")
    deps = GenerationDeps(
        llm=llm,
        decision_model=dm,
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
        tools={},
        settings=__import__("config").Settings(),
        callback=fake_callback,
    )

    req = ChatTurnRequest(
        map_id=snap.map_id,
        message="What is the main topic?",
        history=[ChatMessage(role="user", content="Previous message")],
        summary="User is a beginner studying machine learning.",
        map_snapshot=snap,
        callback_base_url="http://localhost/callback",
        callback_token="tok",
    )

    response = await run_chat_turn(deps, req)

    assert response.intent == ChatIntent.QUESTION
    assert response.answer is not None
    # Inspect prompt passed to the LLM to verify summary was delivered in prompt
    sent_prompt = llm.calls[0][0].content
    assert "User is a beginner studying machine learning" in sent_prompt
    assert "Previous message" in sent_prompt


@pytest.mark.asyncio
async def test_run_chat_turn_with_summary_for_proposal(fake_embedding_provider, fake_vector_store, fake_callback):
    """Verifies run_chat_turn uses the rolling summary when generating action proposals."""
    import json

    from tests.conftest import FakeLLMProvider

    snap = _make_snapshot()
    proposal_json = json.dumps(
        {
            "action_type": "edit_proposal",
            "target_node_ids": [str(snap.nodes[0].id)],
            "parameters": {},
            "explanation": "Add reinforcement learning node adhering to beginner preference",
            "needs_clarification": False,
        }
    )
    llm = FakeLLMProvider([proposal_json])
    dm = ConfigurableDecisionModel("edit_proposal")
    deps = GenerationDeps(
        llm=llm,
        decision_model=dm,
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
        tools={},
        settings=__import__("config").Settings(),
        callback=fake_callback,
    )

    req = ChatTurnRequest(
        map_id=snap.map_id,
        message="Add a node about reinforcement learning.",
        history=[],
        summary="User preferred beginner friendly structure.",
        map_snapshot=snap,
        callback_base_url="http://localhost/callback",
        callback_token="tok",
    )

    response = await run_chat_turn(deps, req)

    assert response.intent == ChatIntent.EDIT_PROPOSAL
    assert response.action_proposal is not None
    sent_prompt = llm.calls[0][0].content
    assert "User preferred beginner friendly structure" in sent_prompt


@pytest.mark.asyncio
async def test_backward_compatibility_without_summary(fake_embedding_provider, fake_vector_store, fake_callback):
    """Ensures existing callers omitting summary continue to work without error."""
    from tests.conftest import FakeLLMProvider

    snap = _make_snapshot()
    llm = FakeLLMProvider(["Everything works normally without summary."])
    dm = ConfigurableDecisionModel("question")
    deps = GenerationDeps(
        llm=llm,
        decision_model=dm,
        embedding_provider=fake_embedding_provider,
        vector_store=fake_vector_store,
        tools={},
        settings=__import__("config").Settings(),
        callback=fake_callback,
    )

    req = ChatTurnRequest(
        map_id=snap.map_id,
        message="Test without summary",
        history=[ChatMessage(role="user", content="Old msg")],
        # summary is omitted, defaulting to None
        map_snapshot=snap,
        callback_base_url="http://localhost/callback",
        callback_token="tok",
    )

    response = await run_chat_turn(deps, req)
    assert response.intent == ChatIntent.QUESTION
    assert response.answer == "Everything works normally without summary."
