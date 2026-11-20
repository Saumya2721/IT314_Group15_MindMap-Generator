"""S3-L7: provider-aware token allocation and deterministic pruning."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from orchestration.context_budget import ContextBudgetManager, provider_limits


def chunk(size: int, score: float):
    return SimpleNamespace(record=SimpleNamespace(chunk_text="r" * size), relevance_score=score)


def render(ctx):
    return "\n".join(
        [
            ctx.seed,
            ctx.memory_text,
            *[c.record.chunk_text for c in ctx.rag_chunks],
            *ctx.history,
            *ctx.external_payloads,
        ]
    )


def test_provider_ceilings_and_qwen_otpm():
    assert provider_limits("groq", "llama-3.3-70b-versatile").input_ceiling == 6000
    assert provider_limits("groq", "qwen-2.5").output_ceiling == 900
    assert provider_limits("gemini", "gemini-2.5-flash").input_ceiling == 32000
    assert provider_limits("gemini", "gemini-2.5-flash").context_window == 128000
    assert ContextBudgetManager("groq", "qwen-2.5", 8000).max_output_tokens == 900


def test_token_slots_and_memories_never_exceed_ten_percent():
    manager = ContextBudgetManager("groq", "llama", 2000)
    ctx = manager.allocate(
        system_prompt="Instructions",
        seed="s" * 3200,
        rag_chunks=[chunk(2000, 0.9)],
        memories=["m" * 2500] * 3,
        history=["h" * 400],
        render=render,
    )
    assert ctx.breakdown["input_total"] <= ctx.input_limit
    assert ctx.breakdown["memories"] <= ctx.input_limit // 10
    assert ctx.breakdown["input_limit"] == manager.input_limit


def test_rag_pruned_before_chat_and_external_lowest_score_first():
    manager = ContextBudgetManager("groq", "llama", 2048)
    ctx = manager.allocate(
        system_prompt="sys",
        seed="s" * 8000,
        rag_chunks=[chunk(11000, 0.9), chunk(11000, 0.1)],
        history=["old" * 100, "recent" * 100],
        external_payloads=["w" * 1000],
        render=render,
    )
    assert ctx.pruned[0] == "rag"
    assert ctx.rag_chunks and ctx.rag_chunks[0].relevance_score == 0.9
    assert ctx.breakdown["input_total"] <= ctx.input_limit


def test_pruning_hierarchy_rag_then_old_history_then_external():
    manager = ContextBudgetManager("groq", "llama", 2048, active_history_window=1)
    ctx = manager.allocate(
        system_prompt="sys",
        seed="s" * 19000,
        rag_chunks=[chunk(4000, 0.5)],
        history=["old" * 900, "recent" * 200],
        external_payloads=["x" * 4000],
        render=render,
    )
    assert ctx.pruned[:3] == ["rag", "history", "external"]
    assert ctx.history == ["recent" * 200]
    assert ctx.breakdown["input_total"] <= ctx.input_limit


def test_final_fallback_truncates_seed_only_after_optional_slots():
    manager = ContextBudgetManager("groq", "llama", 2048)
    ctx = manager.allocate(system_prompt="sys", seed="S" * 50000, rag_chunks=[], render=render)
    assert "seed" in ctx.pruned
    assert 0 < len(ctx.seed) < 50000
    assert ctx.breakdown["input_total"] <= ctx.input_limit


def test_system_prompt_cannot_silently_be_deleted():
    manager = ContextBudgetManager("groq", "llama", 2048)
    with pytest.raises(ValueError, match="System prompt"):
        manager.allocate(system_prompt="x" * 27000, seed="topic", rag_chunks=[], render=render)


def test_rendered_context_headers_are_counted():
    manager = ContextBudgetManager("groq", "llama", 2048)

    def with_headers(ctx):
        return render(ctx) + "H" * 20000

    ctx = manager.allocate(system_prompt="sys", seed="seed", rag_chunks=[chunk(4000, 0.4)], render=with_headers)
    assert not ctx.rag_chunks
    assert ctx.breakdown["input_total"] <= ctx.input_limit


@pytest.mark.asyncio
async def test_generate_node_logs_budget_and_uses_bounded_context():
    from unittest.mock import AsyncMock, MagicMock

    from orchestration.nodes import make_nodes

    deps = MagicMock()
    deps.settings.generation.enable_two_pass = False
    deps.settings.generation.enable_dynamic_prompts = False
    deps.settings.llm.provider = "groq"
    deps.settings.llm.model = "llama-3.3-70b-versatile"
    deps.settings.llm.max_output_tokens = 8000
    deps.llm.generate = AsyncMock(return_value=SimpleNamespace(text='{"nodes": [], "edges": []}'))
    deps.callback.progress = AsyncMock()
    state = {
        "seed_text": "A short lesson on neural networks.",
        "resolved_detail_level": "balanced",
        "retrieved_chunks": [chunk(22000, 0.2), chunk(500, 0.9)],
        "prompt_items_log": [],
    }

    result = await make_nodes(deps)["generate"](state)
    allocation = result["prompt_items_log"][-1]
    assert allocation["type"] == "token_budget"
    assert allocation["allocation"]["input_total"] <= allocation["allocation"]["input_limit"]
    assert allocation["pruned"] == ["rag"]
    assert len(result["retrieved_chunks"]) == 1
    assert result["retrieved_chunks"][0].relevance_score == 0.9
    assert deps.llm.generate.await_args.kwargs["max_tokens"] == 2048
