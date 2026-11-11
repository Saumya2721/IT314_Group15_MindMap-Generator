from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from memory.callback_handler import MemoryCallbackHandler
from memory.context_assembler import MemoryContextAssembler
from memory.schemas import CallbackPayload, MemoryContext, TokenBudget
from memory.service import MemoryService
from memory.summarizer import RollingSummarizer
from memory.token_budget import (
    TokenBudgetManager,
    estimate_message_tokens,
    estimate_tokens,
)
from routes.internal import chat_callback_router


def test_token_budget_estimation():
    assert estimate_tokens("") == 0
    assert estimate_tokens("1234") == 1
    assert estimate_tokens("a" * 40) == 10
    assert estimate_message_tokens("user", "") == 5


def test_token_budget_manager_limits():
    budget = TokenBudget(
        max_total_tokens=4000,
        max_history_tokens=2000,
        max_summary_tokens=500,
        reserved_for_response=1000,
    )
    mgr = TokenBudgetManager(budget)
    assert mgr.get_available_for_history() == 2000

    tight = TokenBudget(
        max_total_tokens=2000,
        max_history_tokens=2000,
        max_summary_tokens=500,
        reserved_for_response=1000,
    )
    tight_mgr = TokenBudgetManager(tight)
    assert tight_mgr.get_available_for_history() == 500


def test_token_budget_trimming():
    budget = TokenBudget(max_total_tokens=4000)
    mgr = TokenBudgetManager(budget)
    messages = [
        {"role": "user", "content": "First message old"},
        {"role": "assistant", "content": "Second message"},
        {"role": "user", "content": "Third message latest"},
    ]
    trimmed, was_trunc = mgr.trim_messages_to_budget(messages, 15)
    assert was_trunc is True
    assert len(trimmed) == 1
    assert "Third" in trimmed[0]["content"]

    empty_trimmed, empty_trunc = mgr.trim_messages_to_budget([], 100)
    assert len(empty_trimmed) == 0
    assert empty_trunc is False


def test_rolling_summarizer_threshold():
    summarizer = RollingSummarizer(max_messages_before_summary=5)
    short_convo = [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello"}]
    assert summarizer.needs_summary(short_convo) is False

    long_convo = [{"role": "user", "content": f"Message {i}"} for i in range(6)]
    assert summarizer.needs_summary(long_convo) is True


def test_rolling_summarizer_prompt():
    summarizer = RollingSummarizer(max_messages_before_summary=5)
    convo = [{"role": "user", "content": "hello"}]
    prompt = summarizer.build_summary_prompt(convo, existing_summary="Prior summary")
    assert "[PREVIOUS SUMMARY]" in prompt
    assert "Prior summary" in prompt
    assert "[CONVERSATION]" in prompt


def test_rolling_summarizer_fallback():
    async def _test():
        summarizer = RollingSummarizer()
        messages = [
            {"role": "user", "content": "Create a mind map on cellular biology"},
            {"role": "assistant", "content": "Generated 5 nodes"},
            {"role": "user", "content": "Expand the mitochondria branch"},
        ]
        summary = await summarizer.generate_summary(messages, llm_provider=None)
        assert "cellular biology" in summary
        assert "mitochondria" in summary

    asyncio.run(_test())


def test_rolling_summarizer_llm_call():
    async def _test():
        summarizer = RollingSummarizer()
        mock_llm = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = "AI-generated summary: user explored cellular biology."
        mock_llm.generate = AsyncMock(return_value=mock_resp)

        messages = [{"role": "user", "content": "Biology map"}]
        summary = await summarizer.generate_summary(messages, llm_provider=mock_llm)
        assert "AI-generated summary" in summary
        mock_llm.generate.assert_awaited_once()

    asyncio.run(_test())


def test_context_assembler():
    async def _test():
        assembler = MemoryContextAssembler()
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "Generate a map of India"},
        ]
        context = await assembler.assemble(
            map_id="map-123", raw_messages=messages, existing_summary="Previous overview"
        )
        assert isinstance(context, MemoryContext)
        assert context.summary == "Previous overview"
        assert len(context.messages) == 3
        assert context.total_tokens > 0
        assert context.budget_remaining > 0

        formatted = assembler.format_for_llm(context)
        assert "[CONVERSATION CONTEXT]" in formatted
        assert "Summary: Previous overview" in formatted
        assert "Generate a map of India" in formatted

    asyncio.run(_test())


def test_memory_callback_handler():
    async def _test():
        handler = MemoryCallbackHandler()
        payload = CallbackPayload(
            map_id="map-test-1",
            job_id="job-test-1",
            status="completed",
            summary="A concise rolling summary of the mind map topic.",
        )
        result = await handler.handle_completion(payload)
        assert result["status"] == "acknowledged"
        assert result["action"] == "summary_stored"
        assert handler.get_cached_summary("map-test-1") == "A concise rolling summary of the mind map topic."

        handler.clear_summary("map-test-1")
        assert handler.get_cached_summary("map-test-1") is None

    asyncio.run(_test())


def test_memory_service_orchestration():
    async def _test():
        service = MemoryService()
        messages = [{"role": "user", "content": "Create physics map"}]
        context = await service.build_context("map-phys", messages)
        assert isinstance(context, MemoryContext)
        assert len(context.messages) == 1
        assert "physics map" in service.format_context_for_llm(context)

    asyncio.run(_test())


def test_internal_chat_completion_callback():
    app = FastAPI()
    app.include_router(chat_callback_router)
    client = TestClient(app)

    job_id = uuid4()
    map_id = uuid4()

    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = {"id": job_id, "status": "running"}
    mock_conn.fetchval.return_value = uuid4()
    mock_pool.fetchrow.return_value = {
        "id": job_id,
        "map_id": map_id,
        "job_type": "generate",
        "status": "running",
        "callback_token_hash": "dummyhash",
    }

    class MockTx:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *args):
            return None

    app.state.pool = mock_pool

    with (
        patch("routes.internal.verify_callback_auth", AsyncMock(return_value={"map_id": map_id})),
        patch("routes.internal.transaction", return_value=MockTx()),
        patch("routes.internal.get_or_create_chat_thread", AsyncMock(return_value=uuid4())),
        patch("routes.internal.insert_chat_message", AsyncMock()),
        patch("routes.internal.update_chat_thread_summary", AsyncMock()),
    ):
        resp = client.post(
            f"/internal/chat/{job_id}/complete",
            json={
                "map_id": str(map_id),
                "job_id": str(job_id),
                "assistant_message": "Mind map completed with 12 nodes.",
                "summary": "Topics discussed: physics mechanics.",
                "status": "completed",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["summary_updated"] is True
