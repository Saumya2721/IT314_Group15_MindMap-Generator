"""
Unit tests for S3-L1 — Tool Policy Layer.

Covers:
- Per-tool timeouts (ToolTimeoutError)
- Enable / disable flags loaded from config (ToolDisabledError & wrap_tool_registry filtering)
- Per-user quotas enforced via ToolQuotaLimiter (in-memory & Redis pipeline mock)
- Overall research time budget tracking (ResearchTimeBudget & ResearchBudgetExceededError)
- Policy enforcement in execute_research_queries (S3-A2)
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from config import Settings, ToolConfig
from providers.base import ToolSpec
from tools.base import Tool, ToolResult
from tools.policy import (
    PolicyEnforcedTool,
    ResearchBudgetExceededError,
    ResearchTimeBudget,
    ToolDisabledError,
    ToolPolicy,
    ToolQuotaExceededError,
    ToolQuotaLimiter,
    ToolTimeoutError,
    load_tool_policy,
    wrap_tool_registry,
)
from tools.research_planner import execute_research_queries

# ---------------------------------------------------------------------------
# Test Stubs
# ---------------------------------------------------------------------------


class DummyTool(Tool):
    """Simple test tool with configurable delay and return values."""

    def __init__(self, name: str = "dummy", delay_s: float = 0.0, results: list[ToolResult] | None = None):
        self._name = name
        self.delay_s = delay_s
        self.results = results or [ToolResult(content="content", source_title="Title", source_url="http://test.com")]
        self.call_count = 0

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name=self._name, description="Test tool", parameters={})

    async def invoke(self, **kwargs: Any) -> list[ToolResult]:
        self.call_count += 1
        if self.delay_s > 0:
            await asyncio.sleep(self.delay_s)
        return self.results


# ---------------------------------------------------------------------------
# 1. Per-Tool Timeouts
# ---------------------------------------------------------------------------


class TestToolTimeouts:
    @pytest.mark.asyncio
    async def test_tool_completes_within_timeout(self) -> None:
        dummy = DummyTool(delay_s=0.01)
        policy = ToolPolicy(name="fast_tool", timeout_s=0.5)
        enforced = PolicyEnforcedTool(dummy, policy)

        results = await enforced.invoke(query="test")
        assert len(results) == 1
        assert dummy.call_count == 1

    @pytest.mark.asyncio
    async def test_tool_times_out(self) -> None:
        dummy = DummyTool(delay_s=0.2)
        policy = ToolPolicy(name="slow_tool", timeout_s=0.05)
        enforced = PolicyEnforcedTool(dummy, policy)

        with pytest.raises(ToolTimeoutError) as exc_info:
            await enforced.invoke(query="test")

        assert "slow_tool" in str(exc_info.value)
        assert "timed out" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 2. Enable / Disable Flags
# ---------------------------------------------------------------------------


class TestToolEnableDisable:
    @pytest.mark.asyncio
    async def test_disabled_tool_raises_disabled_error(self) -> None:
        dummy = DummyTool()
        policy = ToolPolicy(name="disabled_tool", enabled=False)
        enforced = PolicyEnforcedTool(dummy, policy)

        with pytest.raises(ToolDisabledError) as exc_info:
            await enforced.invoke(query="test")

        assert "disabled by configuration" in str(exc_info.value)
        assert dummy.call_count == 0

    def test_wrap_tool_registry_omits_disabled_tools(self) -> None:
        t1 = DummyTool(name="arxiv_search")
        t2 = DummyTool(name="web_search")
        registry = {"arxiv_search": t1, "web_search": t2}

        # Settings with arxiv enabled, web_search disabled
        tools_cfg = ToolConfig(
            arxiv_search_enabled=True,
            arxiv_search_timeout_s=15.0,
            web_search_enabled=False,
            web_search_timeout_s=10.0,
            web_search_api_key="key",
            user_quota_calls=10,
            user_quota_window_s=60,
        )
        settings = Settings(tools=tools_cfg)

        wrapped = wrap_tool_registry(registry, settings)
        assert "arxiv_search" in wrapped
        assert "web_search" not in wrapped
        assert isinstance(wrapped["arxiv_search"], PolicyEnforcedTool)

    def test_load_tool_policy_from_settings(self) -> None:
        tools_cfg = ToolConfig(
            arxiv_search_enabled=True,
            arxiv_search_timeout_s=20.0,
            web_search_enabled=True,
            web_search_api_key="test_key",
            web_search_timeout_s=12.0,
            user_quota_calls=25,
            user_quota_window_s=45,
        )
        settings = Settings(tools=tools_cfg)

        arxiv_policy = load_tool_policy("arxiv_search", settings)
        assert arxiv_policy.enabled is True
        assert arxiv_policy.timeout_s == 20.0
        assert arxiv_policy.quota_calls == 25

        web_policy = load_tool_policy("web_search", settings)
        assert web_policy.enabled is True
        assert web_policy.timeout_s == 12.0


# ---------------------------------------------------------------------------
# 3. Per-User Quota Limiter (S3-BE5)
# ---------------------------------------------------------------------------


class TestUserQuotaLimiter:
    @pytest.mark.asyncio
    async def test_in_memory_quota_enforced(self) -> None:
        limiter = ToolQuotaLimiter(redis_client=None)
        user_id = "user-123"
        tool_name = "web_search"

        # Allow 2 calls per 10s
        for _ in range(2):
            ok = await limiter.check_and_consume(user_id, tool_name, max_calls=2, window_s=10)
            assert ok is True

        # 3rd call should be rejected
        ok = await limiter.check_and_consume(user_id, tool_name, max_calls=2, window_s=10)
        assert ok is False

    @pytest.mark.asyncio
    async def test_different_users_have_independent_quotas(self) -> None:
        limiter = ToolQuotaLimiter(redis_client=None)

        assert await limiter.check_and_consume("user-a", "web_search", max_calls=1, window_s=60) is True
        assert await limiter.check_and_consume("user-a", "web_search", max_calls=1, window_s=60) is False

        # user-b is still fresh
        assert await limiter.check_and_consume("user-b", "web_search", max_calls=1, window_s=60) is True

    @pytest.mark.asyncio
    async def test_policy_enforced_tool_raises_quota_exceeded(self) -> None:
        dummy = DummyTool()
        policy = ToolPolicy(name="quota_tool", quota_calls=1, quota_window_s=60)
        limiter = ToolQuotaLimiter(redis_client=None)
        enforced = PolicyEnforcedTool(dummy, policy, limiter=limiter)

        # Call 1 succeeds
        await enforced.invoke(user_id="user-xyz", query="q1")
        assert dummy.call_count == 1

        # Call 2 raises ToolQuotaExceededError
        with pytest.raises(ToolQuotaExceededError) as exc_info:
            await enforced.invoke(user_id="user-xyz", query="q2")

        assert "exceeded quota" in str(exc_info.value)
        assert dummy.call_count == 1

    @pytest.mark.asyncio
    async def test_redis_quota_sliding_window(self) -> None:
        mock_redis = MagicMock()
        mock_redis.eval = AsyncMock(side_effect=[1, 0])
        limiter = ToolQuotaLimiter(redis_client=mock_redis)
        assert await limiter.check_and_consume("usr_redis", "arxiv", max_calls=3, window_s=60)
        assert not await limiter.check_and_consume("usr_redis", "arxiv", max_calls=3, window_s=60)

    @pytest.mark.asyncio
    async def test_redis_failure_denies_tool_work(self) -> None:
        mock_redis = MagicMock()
        mock_redis.eval = AsyncMock(side_effect=RuntimeError("Redis connection lost"))
        limiter = ToolQuotaLimiter(redis_client=mock_redis)
        assert not await limiter.check_and_consume("usr_redis", "arxiv", max_calls=2, window_s=60)


# ---------------------------------------------------------------------------
# 4. Overall Research Time Budget (S3-A2)
# ---------------------------------------------------------------------------


class TestResearchTimeBudget:
    def test_budget_initial_state(self) -> None:
        budget = ResearchTimeBudget(budget_s=10.0)
        assert budget.remaining_s > 9.0
        assert budget.is_expired() is False
        budget.check()  # does not raise

    def test_budget_expiration(self) -> None:
        budget = ResearchTimeBudget(budget_s=0.01)
        time.sleep(0.02)
        assert budget.is_expired() is True

        with pytest.raises(ResearchBudgetExceededError) as exc_info:
            budget.check()
        assert "Overall research time budget" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_enforced_tool_halts_when_budget_expired(self) -> None:
        dummy = DummyTool()
        policy = ToolPolicy(name="budget_tool")
        enforced = PolicyEnforcedTool(dummy, policy)

        budget = ResearchTimeBudget(budget_s=0.01)
        time.sleep(0.02)

        with pytest.raises(ResearchBudgetExceededError):
            await enforced.invoke(budget=budget, query="test")

        assert dummy.call_count == 0


# ---------------------------------------------------------------------------
# 5. execute_research_queries with Budget and Policy
# ---------------------------------------------------------------------------


class TestExecuteResearchQueriesPolicy:
    @pytest.mark.asyncio
    async def test_execute_research_queries_stops_on_exhausted_budget(self) -> None:
        dummy = DummyTool()
        budget = ResearchTimeBudget(budget_s=0.01)
        time.sleep(0.02)

        results = await execute_research_queries(
            tool=dummy,
            queries=["query 1", "query 2"],
            budget=budget,
        )
        assert results == []
        assert dummy.call_count == 0

    @pytest.mark.asyncio
    async def test_execute_research_queries_passes_budget_and_user_id(self) -> None:
        dummy = DummyTool()
        policy = ToolPolicy(name="arxiv_search", timeout_s=5.0)
        enforced = PolicyEnforcedTool(dummy, policy)
        budget = ResearchTimeBudget(budget_s=5.0)

        results = await execute_research_queries(
            tool=enforced,
            queries=["quantum algorithms", "neural nets"],
            budget=budget,
            user_id="user-test-uuid",
        )
        assert len(results) >= 1
        assert dummy.call_count == 2
