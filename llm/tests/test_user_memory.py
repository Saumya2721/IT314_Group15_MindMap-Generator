"""
Unit tests for S3-L3: User Memory Extraction, Scoring, Retrieval, Prompt Injection,
and Workflow Integration.

Covers:
- Memory scoring and deduplication logic
- Extraction from interactions (mocked LLM)
- Memory retrieval (mocked HTTP client)
- Prompt injection with token ceiling enforcement
- Integration in generate_mindmap workflow
- Integration in chat_turn_task workflow
- Graceful degradation when backend is unavailable
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from memory.prompt import (
    get_memory_token_ceiling,
    inject_memories_into_system_prompt,
    select_top_k_relevant_memories,
)
from memory.schemas import ProposedMemory, UserMemory
from memory.scoring import (
    calculate_importance_score,
    deduplicate_and_filter_proposals,
    is_exact_duplicate,
    is_near_duplicate,
    is_transient_or_noise,
    normalize_memory_text,
    token_jaccard_similarity,
)

# ---------------------------------------------------------------------------
# Shared Fixtures
# ---------------------------------------------------------------------------


class FakeSettings:
    """Minimal Settings stand-in for tests that need deps.settings.llm.* attributes.

    Uses the real Settings dataclass with its default-from-env values so there
    is nothing to hard-code and no actual network calls are needed.
    """

    def __init__(self) -> None:
        from config import Settings  # local import — avoids circular issues at collection

        _real = Settings()
        self.llm = _real.llm
        self.generation = _real.generation
        self.decision_model = _real.decision_model
        self.embedding = _real.embedding
        self.pinecone = _real.pinecone
        self.tools = _real.tools
        self.arq = _real.arq
        self.langsmith = _real.langsmith
        self.vision = _real.vision


TEST_USER_ID = uuid4()
TEST_MAP_ID = uuid4()


def _make_memory(
    content: str,
    memory_type: str = "preference",
    is_active: bool = True,
    importance_score: float = 0.8,
) -> UserMemory:
    return UserMemory(
        id=uuid4(),
        user_id=TEST_USER_ID,
        map_id=TEST_MAP_ID,
        memory_type=memory_type,
        content=content,
        is_active=is_active,
        created_at=datetime.now(UTC),
        importance_score=importance_score,
    )


def _make_proposed(
    content: str,
    memory_type: str = "preference",
    importance_score: float = 0.8,
    is_durable: bool = True,
) -> ProposedMemory:
    return ProposedMemory(
        content=content,
        memory_type=memory_type,
        importance_score=importance_score,
        is_durable=is_durable,
    )


# ---------------------------------------------------------------------------
# 1. Deduplication and Scoring Tests
# ---------------------------------------------------------------------------


def test_normalize_memory_text():
    assert normalize_memory_text("  Hello World  ") == "hello world"
    assert normalize_memory_text("User  prefers  concise  output") == "user prefers concise output"


def test_exact_duplicate_detection():
    existing = ["User prefers bullet points"]
    assert is_exact_duplicate("User prefers bullet points", existing)
    assert is_exact_duplicate("  USER PREFERS BULLET POINTS  ", existing)
    assert not is_exact_duplicate("User prefers numbered lists", existing)


def test_exact_duplicate_with_memory_objects():
    mem = _make_memory("Concise technical definitions preferred")
    assert is_exact_duplicate("Concise technical definitions preferred", [mem])
    assert not is_exact_duplicate("Different content here", [mem])


def test_near_duplicate_detection():
    existing = ["User prefers concise technical definitions with examples"]
    # Very similar text should be detected as near-duplicate
    assert is_near_duplicate("User prefers concise technical definitions with real examples", existing, threshold=0.70)
    # Very different text is not a near-duplicate
    assert not is_near_duplicate("User is a machine learning researcher", existing)


def test_token_jaccard_similarity_exact():
    assert token_jaccard_similarity("hello world", "hello world") == 1.0


def test_token_jaccard_similarity_no_overlap():
    sim = token_jaccard_similarity("quantum computing", "pizza recipe")
    assert sim == 0.0


def test_token_jaccard_similarity_partial():
    sim = token_jaccard_similarity("user prefers dark mode", "user prefers light mode")
    # 3 tokens match (user, prefers, mode out of 5 unique+dark+light = 6 unique)
    assert 0.3 < sim < 1.0


def test_is_transient_or_noise():
    assert is_transient_or_noise("zoom in")
    assert is_transient_or_noise("undo")
    assert is_transient_or_noise("hi")
    assert is_transient_or_noise("hello")
    assert not is_transient_or_noise("User prefers concise technical definitions with examples")
    assert not is_transient_or_noise("User is a PhD student in bioinformatics")


def test_calculate_importance_score_preference():
    score = calculate_importance_score("User prefers bullet points and visual flow", "preference")
    assert 0.75 <= score <= 1.0


def test_calculate_importance_score_decision():
    score = calculate_importance_score("Use formal academic terminology always", "decision")
    assert 0.70 <= score <= 1.0


def test_calculate_importance_score_transient():
    score = calculate_importance_score("zoom in", "preference")
    assert score == 0.2


def test_calculate_importance_score_non_durable():
    score = calculate_importance_score("temporary thing", "fact", is_durable=False)
    assert score == 0.2


def test_calculate_importance_score_recurring_boost():
    base = calculate_importance_score("User likes numbered lists", "preference", is_durable=True)
    recurring = calculate_importance_score(
        "User likes numbered lists", "preference", is_durable=True, is_recurring=True
    )
    assert recurring > base


def test_deduplicate_filters_exact_duplicates():
    existing = [_make_memory("User prefers concise output")]
    candidates = [_make_proposed("User prefers concise output")]
    result = deduplicate_and_filter_proposals(candidates, existing_memories=existing)
    assert len(result) == 0


def test_deduplicate_filters_near_duplicates():
    existing = ["User prefers concise and clear technical explanations"]
    candidates = [_make_proposed("User prefers clear and concise technical explanations")]
    result = deduplicate_and_filter_proposals(candidates, existing_memories=existing, min_importance=0.0)
    assert len(result) == 0


def test_deduplicate_filters_non_durable():
    candidates = [_make_proposed("zoom in", is_durable=False)]
    result = deduplicate_and_filter_proposals(candidates, existing_memories=[])
    assert len(result) == 0


def test_deduplicate_filters_low_importance():
    candidates = [_make_proposed("some info", importance_score=0.3)]
    result = deduplicate_and_filter_proposals(candidates, existing_memories=[], min_importance=0.5)
    assert len(result) == 0


def test_deduplicate_accepts_valid_unique_proposals():
    existing = [_make_memory("User likes visual mind maps")]
    candidates = [
        _make_proposed("User is a PhD researcher in machine learning", importance_score=0.75),
        _make_proposed("User prefers formal academic writing style", importance_score=0.80),
    ]
    result = deduplicate_and_filter_proposals(candidates, existing_memories=existing)
    assert len(result) == 2


def test_deduplicate_handles_multiple_proposals_batch_dedup():
    # Second candidate is near-duplicate of first within the batch itself
    candidates = [
        _make_proposed("User prefers bullet points in summaries", importance_score=0.75),
        _make_proposed("User prefers bullet points in all summaries", importance_score=0.75),
    ]
    result = deduplicate_and_filter_proposals(candidates, existing_memories=[], min_importance=0.5)
    # Only the first should survive; second is near-duplicate of first
    assert len(result) == 1


# ---------------------------------------------------------------------------
# 2. Memory Retrieval and Prompt Injection Tests
# ---------------------------------------------------------------------------


def test_select_top_k_relevant_memories_basic():
    memories = [
        _make_memory("User prefers Python programming", importance_score=0.9),
        _make_memory("User is a PhD student in machine learning", importance_score=0.8),
        _make_memory("User likes detailed explanations about neural networks", importance_score=0.7),
        _make_memory("User prefers concise bullet points", importance_score=0.6),
    ]
    top = select_top_k_relevant_memories(memories, query="Python neural network tutorial", top_k=2)
    assert len(top) == 2
    # Most relevant to Python + neural network should rank high
    contents = [m.content for m in top]
    assert any("Python" in c or "neural" in c or "machine learning" in c for c in contents)


def test_select_top_k_relevant_memories_inactive_excluded():
    memories = [
        _make_memory("Active preference", is_active=True),
        _make_memory("Inactive memory", is_active=False),
    ]
    top = select_top_k_relevant_memories(memories, query="preference", top_k=5)
    assert all(m.is_active for m in top)
    assert len(top) == 1


def test_select_top_k_relevant_memories_empty():
    result = select_top_k_relevant_memories([], query="anything", top_k=5)
    assert result == []


def test_inject_memories_basic():
    system = "You are a helpful AI assistant."
    memories = [_make_memory("User prefers concise explanations")]
    enhanced = inject_memories_into_system_prompt(system, memories, provider="groq", model="llama-3.3-70b-versatile")
    assert "User prefers concise explanations" in enhanced
    assert system in enhanced
    assert "untrusted contextual background" in enhanced.lower() or "CRITICAL" in enhanced


def test_inject_memories_empty_returns_original():
    system = "Original system prompt."
    result = inject_memories_into_system_prompt(system, [], provider="groq", model="llama-3.3-70b-versatile")
    assert result == system


def test_inject_memories_respects_token_ceiling():
    """The 10% ceiling must be enforced: very long memories get trimmed or dropped."""
    system = "Base system prompt."
    # Use raw strings (not UserMemory objects) to bypass the 4000-char Pydantic limit —
    # inject_memories_into_system_prompt accepts Sequence[UserMemory | str].
    huge_memory_line = "User preferences include: " + ("very detailed description " * 200)
    memories: list[str] = [
        huge_memory_line,
        "Short preference about style",
    ]
    enhanced = inject_memories_into_system_prompt(system, memories, provider="groq", model="llama-3.3-70b-versatile")
    # Should not crash; original prompt should still be there
    assert system in enhanced

    # Token ceiling: 10% of groq's 8192 context window = ~819 tokens
    from chat.token_budget import estimate_tokens

    ceiling = get_memory_token_ceiling("groq", "llama-3.3-70b-versatile")
    # The memory injection portion alone should be <= ceiling
    added_tokens = estimate_tokens(enhanced) - estimate_tokens(system)
    assert added_tokens <= ceiling + 50  # small tolerance for rounding


def test_inject_memories_preserves_original_system_prompt():
    """Original system prompt must remain intact."""
    system = "IMPORTANT: Always return JSON only. Never break this rule."
    memories = [_make_memory("User prefers Python code examples")]
    enhanced = inject_memories_into_system_prompt(system, memories, provider="groq", model="llama-3.3-70b-versatile")
    assert system in enhanced


def test_inject_string_memories():
    """Should work with raw string memories too (not just UserMemory objects)."""
    system = "Base prompt."
    enhanced = inject_memories_into_system_prompt(
        system,
        ["[preference] User prefers dark mode", "[fact] User is a data scientist"],
        provider="groq",
        model="llama-3.3-70b-versatile",
    )
    assert "dark mode" in enhanced
    assert "data scientist" in enhanced


def test_get_memory_token_ceiling_10_percent():
    ceiling = get_memory_token_ceiling("groq", "llama")
    # groq context window = 8192, 10% = 819
    assert ceiling <= 820
    assert ceiling >= 128


# ---------------------------------------------------------------------------
# 3. Async Extraction Tests (mocked LLM)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_memories_success():
    """LLM returns valid extraction JSON → proposals are returned."""
    from memory.extraction import extract_memories_from_interaction

    extraction_output = json.dumps(
        {
            "memories": [
                {
                    "content": "User prefers concise bullet-point explanations",
                    "memory_type": "preference",
                    "importance_score": 0.85,
                    "is_durable": True,
                }
            ]
        }
    )

    mock_llm = AsyncMock()
    mock_llm.generate.return_value = MagicMock(text=extraction_output)

    proposals = await extract_memories_from_interaction(
        llm=mock_llm,
        user_inputs=["Please use concise bullet points when explaining concepts"],
        interaction_summary="Generated mind map about machine learning",
    )
    assert len(proposals) == 1
    assert "bullet" in proposals[0].content.lower()


@pytest.mark.asyncio
async def test_extract_memories_no_useful_content():
    """LLM returns empty memories for transient/trivial interactions."""
    from memory.extraction import extract_memories_from_interaction

    mock_llm = AsyncMock()
    mock_llm.generate.return_value = MagicMock(text='{"memories": []}')

    proposals = await extract_memories_from_interaction(
        llm=mock_llm,
        user_inputs=["zoom in", "undo"],
    )
    assert proposals == []


@pytest.mark.asyncio
async def test_extract_memories_llm_failure_graceful():
    """LLM raises an exception → empty list returned, no crash."""
    from memory.extraction import extract_memories_from_interaction

    mock_llm = AsyncMock()
    mock_llm.generate.side_effect = RuntimeError("LLM unavailable")

    proposals = await extract_memories_from_interaction(
        llm=mock_llm,
        user_inputs=["User prefers Python code examples"],
    )
    assert proposals == []


@pytest.mark.asyncio
async def test_extract_memories_all_transient_inputs():
    """All inputs are transient noise → no LLM call needed, returns empty."""
    from memory.extraction import extract_memories_from_interaction

    mock_llm = AsyncMock()
    # LLM should not even be called if all inputs are transient
    proposals = await extract_memories_from_interaction(
        llm=mock_llm,
        user_inputs=["zoom in", "undo"],
    )
    assert proposals == []
    mock_llm.generate.assert_not_called()


@pytest.mark.asyncio
async def test_extract_memories_dedup_against_existing():
    """Proposals that duplicate existing memories are filtered out."""
    from memory.extraction import extract_memories_from_interaction

    extraction_output = json.dumps(
        {
            "memories": [
                {
                    "content": "User prefers concise bullet points",
                    "memory_type": "preference",
                    "importance_score": 0.8,
                    "is_durable": True,
                }
            ]
        }
    )

    mock_llm = AsyncMock()
    mock_llm.generate.return_value = MagicMock(text=extraction_output)

    existing = [_make_memory("User prefers concise bullet points")]
    proposals = await extract_memories_from_interaction(
        llm=mock_llm,
        user_inputs=["Please use bullet points"],
        existing_memories=existing,
    )
    assert proposals == []  # duplicate of existing


# ---------------------------------------------------------------------------
# 4. Memory Client Tests (mocked HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_client_retrieve_success():
    """Successful HTTP retrieval returns UserMemory objects."""
    from memory.client import MemoryClient

    mem_data = {
        "items": [
            {
                "id": str(uuid4()),
                "user_id": str(TEST_USER_ID),
                "map_id": None,
                "memory_type": "preference",
                "content": "User prefers dark mode",
                "is_active": True,
                "created_at": datetime.now(UTC).isoformat(),
                "importance_score": 0.8,
            }
        ]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mem_data

    client = MemoryClient(base_url="http://test-backend", token="test-token")
    with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        memories = await client.retrieve_user_memories(user_id=TEST_USER_ID)

    assert len(memories) == 1
    assert memories[0].content == "User prefers dark mode"
    await client.aclose()


@pytest.mark.asyncio
async def test_memory_client_retrieve_empty_response():
    """Backend returns 200 with empty items → empty list."""
    from memory.client import MemoryClient

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"items": [], "total": 0}

    client = MemoryClient(base_url="http://test-backend", token="test-token")
    with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        memories = await client.retrieve_user_memories(user_id=TEST_USER_ID)

    assert memories == []
    await client.aclose()


@pytest.mark.asyncio
async def test_memory_client_retrieve_backend_unavailable():
    """Network error → empty list, no crash."""
    import httpx
    from memory.client import MemoryClient

    client = MemoryClient(base_url="http://unreachable-backend", token="test-token")
    with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = httpx.ConnectError("connection refused")
        memories = await client.retrieve_user_memories(user_id=TEST_USER_ID)

    assert memories == []
    await client.aclose()


@pytest.mark.asyncio
async def test_memory_client_propose_success():
    """Successful proposal → MemoryProposalResponse(stored=True)."""
    from memory.client import MemoryClient

    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = {"stored": True, "reason": "created", "id": str(uuid4())}

    client = MemoryClient(base_url="http://test-backend", token="test-token")
    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        result = await client.propose_memory(
            user_id=TEST_USER_ID,
            content="User prefers concise explanations",
            memory_type="preference",
        )

    assert result.stored is True
    assert result.reason == "created"
    await client.aclose()


@pytest.mark.asyncio
async def test_memory_client_propose_backend_unavailable():
    """Network error → stored=False with reason, no crash."""
    import httpx
    from memory.client import MemoryClient

    client = MemoryClient(base_url="http://unreachable-backend", token="test-token")
    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.ConnectError("connection refused")
        result = await client.propose_memory(
            user_id=TEST_USER_ID,
            content="User prefers bullet points",
        )

    assert result.stored is False
    assert "transport" in result.reason or "error" in result.reason.lower()
    await client.aclose()


# ---------------------------------------------------------------------------
# 5. Workflow Integration Tests
# ---------------------------------------------------------------------------


def _make_ctx(llm_responses: list[str] | None = None) -> dict[str, Any]:
    """Build a minimal task context for ARQ task tests."""
    from tests.conftest import FakeLLMProvider, FakeVectorStore

    llm = FakeLLMProvider(llm_responses or ["{}"])
    settings = FakeSettings()
    return {
        "llm": llm,
        "decision_model": MagicMock(),
        "embedding_provider": MagicMock(),
        "vector_store": FakeVectorStore(),
        "tools": [],
        "settings": settings,
        "redis": None,
        "callback": None,
    }


@pytest.mark.asyncio
async def test_generate_mindmap_memory_injection_graceful_no_user_id():
    """generate_mindmap completes normally when user_id is absent (no memory retrieval)."""
    from uuid import uuid4

    from worker.tasks import generate_mindmap

    ctx = _make_ctx(["{}"])
    # Patch retrieve_and_inject_memories to verify it is not called when user_id is None
    with patch("worker.tasks.retrieve_and_inject_memories", new_callable=AsyncMock) as mock_retrieve:
        mock_retrieve.return_value = ("", [])
        from tests.conftest import FakeCallbackClient

        cb = FakeCallbackClient()
        ctx["callback"] = cb

        req = {
            "job_id": str(uuid4()),
            "map_id": str(uuid4()),
            "job_type": "generate",
            "topic": "Machine Learning",
            "callback_base_url": "https://api.test",
            "callback_token": "token",
            "user_id": None,  # No user ID
        }
        # Should not crash
        await generate_mindmap(ctx, req)
        # retrieve_and_inject_memories was called but with user_id=None → returned early
        mock_retrieve.assert_called_once()


@pytest.mark.asyncio
async def test_chat_turn_memory_injection_applied():
    """chat_turn_task passes long_term_memories into request.memory_context."""
    from contracts.models import ChatIntent
    from worker.tasks import chat_turn_task

    mock_memories = [_make_memory("User prefers Python code examples")]

    with (
        patch("worker.tasks.retrieve_and_inject_memories", new_callable=AsyncMock) as mock_retrieve,
        patch("worker.tasks.fire_and_forget_memory_extraction") as mock_extract,  # noqa: F841
        patch("worker.tasks.run_chat_turn", new_callable=AsyncMock) as mock_chat,
    ):
        from contracts.models import ChatTurnResponse

        mock_retrieve.return_value = ("", mock_memories)
        mock_chat.return_value = ChatTurnResponse(
            intent=ChatIntent.QUESTION,
            answer="Machine learning is a subfield of AI.",
        )

        from tests.conftest import FakeCallbackClient

        ctx = _make_ctx()
        cb = FakeCallbackClient()
        ctx["callback"] = cb

        req = {
            "job_id": str(uuid4()),
            "map_id": str(uuid4()),
            "job_type": "chat_turn",
            "message": "Explain machine learning",
            "callback_base_url": "https://api.test",
            "callback_token": "token",
            "memory_context": {"user_id": str(TEST_USER_ID), "summary": None},
        }
        await chat_turn_task(ctx, req)

        # Verify memories were retrieved
        mock_retrieve.assert_called_once()
        # Verify chat was called with updated request (run_chat_turn got the enriched request)
        mock_chat.assert_called_once()
        # The first argument to run_chat_turn should be deps; second is request
        passed_request = mock_chat.call_args[0][1]
        # memory_context should now contain long_term_memories
        assert "long_term_memories" in (passed_request.memory_context or {})


@pytest.mark.asyncio
async def test_chat_turn_memory_extraction_fires_after_success():
    """fire_and_forget_memory_extraction is scheduled after a successful chat turn."""
    from contracts.models import ChatIntent
    from worker.tasks import chat_turn_task

    mock_memories = [_make_memory("User prefers concise explanations")]

    with (
        patch("worker.tasks.retrieve_and_inject_memories", new_callable=AsyncMock) as mock_retrieve,
        patch("worker.tasks.fire_and_forget_memory_extraction") as mock_extract,  # used below
        patch("worker.tasks.run_chat_turn", new_callable=AsyncMock) as mock_chat,
    ):
        from contracts.models import ChatTurnResponse

        mock_retrieve.return_value = ("", mock_memories)
        mock_chat.return_value = ChatTurnResponse(
            intent=ChatIntent.SMALL_TALK,
            answer="Hello! I am here to help.",
        )

        from tests.conftest import FakeCallbackClient

        ctx = _make_ctx()
        cb = FakeCallbackClient()
        ctx["callback"] = cb

        req = {
            "job_id": str(uuid4()),
            "map_id": str(uuid4()),
            "job_type": "chat_turn",
            "message": "Hello!",
            "callback_base_url": "https://api.test",
            "callback_token": "token",
            "memory_context": {"user_id": str(TEST_USER_ID)},
        }
        await chat_turn_task(ctx, req)

        # Extraction should be scheduled because user_id was present
        mock_extract.assert_called_once()


@pytest.mark.asyncio
async def test_workflow_graceful_degradation_memory_backend_down():
    """When memory backend is unreachable, workflows complete normally without crash."""
    import httpx
    from memory.client import MemoryClient

    # Simulate backend completely unreachable at the client level
    client = MemoryClient(base_url="http://unreachable-backend:9999", token="bad-token")
    with patch.object(client._client, "get", side_effect=httpx.ConnectError("refused")):
        memories = await client.retrieve_user_memories(user_id=TEST_USER_ID)
    assert memories == []
    await client.aclose()


@pytest.mark.asyncio
async def test_qa_handler_memory_injection():
    """The QA handler passes memory_context['long_term_memories'] to inject into system prompt."""
    from chat.qa import answer_question
    from contracts.models import ChatTurnRequest, MapSnapshot

    # Patch at the source module because chat.qa uses a lazy local import of inject_memories_into_system_prompt.
    # Patching memory.prompt.inject_memories_into_system_prompt intercepts all callers correctly.
    with patch("memory.prompt.inject_memories_into_system_prompt") as mock_inject:
        mock_inject.side_effect = lambda prompt, _mems, **kw: prompt + " [MEMORY INJECTED]"

        with patch("chat.qa.retrieve_context", new_callable=AsyncMock) as mock_rc:
            with patch("chat.qa.rerank_context", new_callable=AsyncMock) as mock_rr:
                mock_rc.return_value = []
                mock_rr.return_value = []

                from orchestration.deps import GenerationDeps

                from tests.conftest import FakeLLMProvider, FakeVectorStore

                llm = FakeLLMProvider(["Detailed answer about Python."])
                settings = FakeSettings()
                deps = GenerationDeps(
                    llm=llm,
                    decision_model=MagicMock(),
                    embedding_provider=MagicMock(),
                    vector_store=FakeVectorStore(),
                    tools=[],
                    settings=settings,
                    callback=None,
                    redis_client=None,
                )

                request = ChatTurnRequest(
                    job_id=uuid4(),
                    map_id=uuid4(),
                    message="Explain Python",
                    map_snapshot=MapSnapshot(map_id=uuid4()),
                    callback_base_url="https://api.test",
                    callback_token="token",
                    memory_context={"long_term_memories": ["[preference] User prefers Python examples"]},
                )

                await answer_question(deps=deps, request=request)
                # Memory injection should have been called
                mock_inject.assert_called_once()
