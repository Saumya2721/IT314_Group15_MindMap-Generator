"""
Tool Policy Layer (S3-L1).

Provides policy enforcement around external generation tools (FR-29, US-12):
- Per-tool timeouts (asyncio.wait_for)
- Enable/disable flags loaded from backend/llm settings
- Per-user quotas enforced through Redis limiter (with graceful in-memory fallback)
- Overall research time budget tracking (checked inside S3-A2 research execution)
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from typing import Any
from uuid import UUID

from config import Settings
from providers.base import ToolSpec
from tools.base import Tool, ToolError, ToolResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Policy Exceptions
# ---------------------------------------------------------------------------


class ToolPolicyError(ToolError):
    """Base exception for policy violations."""


class ToolDisabledError(ToolPolicyError):
    """Raised when an external tool is disabled by configuration."""


class ToolTimeoutError(ToolPolicyError):
    """Raised when a tool execution exceeds its per-tool timeout."""


class ToolQuotaExceededError(ToolPolicyError):
    """Raised when a user exceeds their quota for tool invocations (S3-BE5)."""


class ResearchBudgetExceededError(ToolPolicyError):
    """Raised when overall research time budget is exhausted (S3-A2)."""


# ---------------------------------------------------------------------------
# Research Time Budget Tracker (S3-A2)
# ---------------------------------------------------------------------------


class ResearchTimeBudget:
    """Tracks cumulative research duration against an overall time budget."""

    def __init__(self, budget_s: float):
        self.budget_s = max(0.001, float(budget_s))
        self.start_time = time.monotonic()

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.start_time

    @property
    def remaining_s(self) -> float:
        return max(0.0, self.budget_s - self.elapsed_s)

    def is_expired(self) -> bool:
        return self.remaining_s <= 0.0

    def check(self) -> None:
        """Raise ResearchBudgetExceededError if overall budget is exhausted."""
        if self.is_expired():
            raise ResearchBudgetExceededError(
                f"Overall research time budget of {self.budget_s:.1f}s exceeded (elapsed {self.elapsed_s:.1f}s)."
            )


# ---------------------------------------------------------------------------
# Redis / In-Memory Per-User Quota Limiter (S3-BE5)
# ---------------------------------------------------------------------------


class ToolQuotaLimiter:
    """Sliding-window per-user quota limiter.

    Uses Redis when available; gracefully falls back to an in-memory sliding
    window if Redis is absent or encounters errors (NFR-07).
    """

    def __init__(self, redis_client: Any = None):
        self._redis = redis_client
        self._local_hits: dict[str, deque[float]] = defaultdict(deque)

    def set_redis(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def check_and_consume(
        self,
        user_key: str | UUID,
        tool_name: str,
        max_calls: int,
        window_s: int,
    ) -> bool:
        """Check if user has remaining quota and record the invocation.

        Returns True if quota is available and consumed, False if exceeded.
        """
        key_str = str(user_key)
        redis_key = f"tool_quota:{key_str}:{tool_name}"

        # 1. Try Redis sliding window via zadd / zremrangebyscore
        if self._redis is not None:
            try:
                now = time.time()
                cutoff = now - window_s
                pipe = self._redis.pipeline()
                pipe.zremrangebyscore(redis_key, 0, cutoff)
                pipe.zcard(redis_key)
                results = await pipe.execute()
                current_count = results[1] if len(results) > 1 else 0

                if current_count >= max_calls:
                    return False

                # Under limit: record call with member = timestamp-uuid
                member = f"{now}-{time.monotonic()}"
                pipe = self._redis.pipeline()
                pipe.zadd(redis_key, {member: now})
                pipe.expire(redis_key, window_s + 10)
                await pipe.execute()
                return True
            except Exception as exc:
                logger.warning("Redis quota limiter error, falling back to in-memory: %s", exc)

        # 2. In-memory sliding window fallback
        now_mono = time.monotonic()
        dq = self._local_hits[f"{key_str}:{tool_name}"]
        while dq and (now_mono - dq[0]) > window_s:
            dq.popleft()

        if len(dq) >= max_calls:
            return False

        dq.append(now_mono)
        return True


# ---------------------------------------------------------------------------
# Tool Policy Definition
# ---------------------------------------------------------------------------


class ToolPolicy:
    """Policy settings for an individual tool."""

    def __init__(
        self,
        name: str,
        enabled: bool = True,
        timeout_s: float = 15.0,
        quota_calls: int = 30,
        quota_window_s: int = 60,
    ):
        self.name = name
        self.enabled = enabled
        self.timeout_s = timeout_s
        self.quota_calls = quota_calls
        self.quota_window_s = quota_window_s


def load_tool_policy(tool_name: str, settings: Settings) -> ToolPolicy:
    """Extract policy parameters for tool_name from global settings."""
    cfg = settings.tools

    if tool_name == "web_search":
        enabled = cfg.web_search_enabled and bool(cfg.web_search_api_key)
        timeout_s = cfg.web_search_timeout_s
    elif tool_name == "arxiv_search":
        enabled = cfg.arxiv_search_enabled
        timeout_s = cfg.arxiv_search_timeout_s
    elif tool_name == "web_reader":
        enabled = cfg.web_reader_enabled
        timeout_s = cfg.web_reader_timeout_s
    elif tool_name == "wikipedia":
        # S3-T1 / fixes #184: Wikipedia research tool (rate limit + timeout via S3-L1)
        enabled = cfg.wikipedia_enabled
        timeout_s = cfg.wikipedia_timeout_s
    else:
        enabled = True
        timeout_s = cfg.tool_timeout_s

    return ToolPolicy(
        name=tool_name,
        enabled=enabled,
        timeout_s=timeout_s,
        quota_calls=cfg.user_quota_calls,
        quota_window_s=cfg.user_quota_window_s,
    )


# ---------------------------------------------------------------------------
# Policy Enforced Tool Wrapper
# ---------------------------------------------------------------------------


class PolicyEnforcedTool(Tool):
    """Wraps a concrete Tool and enforces per-tool timeouts, enable/disable checks,

    per-user quota limits, and overall research time budget.
    """

    def __init__(
        self,
        tool: Tool,
        policy: ToolPolicy,
        limiter: ToolQuotaLimiter | None = None,
    ):
        self._tool = tool
        self.policy = policy
        self._limiter = limiter or ToolQuotaLimiter()

    @property
    def spec(self) -> ToolSpec:
        return self._tool.spec

    async def invoke(
        self,
        *args: Any,
        user_id: str | UUID | None = None,
        budget: ResearchTimeBudget | None = None,
        **kwargs: Any,
    ) -> list[ToolResult]:
        """Invoke tool respecting all policy constraints."""
        # 1. Enable/Disable check
        if not self.policy.enabled:
            raise ToolDisabledError(f"Tool {self.policy.name!r} is disabled by configuration.")

        # 2. Research time budget check
        if budget is not None:
            budget.check()

        # 3. Per-user quota check (Redis limiter S3-BE5)
        if user_id is not None:
            allowed = await self._limiter.check_and_consume(
                user_key=user_id,
                tool_name=self.policy.name,
                max_calls=self.policy.quota_calls,
                window_s=self.policy.quota_window_s,
            )
            if not allowed:
                raise ToolQuotaExceededError(
                    f"User {user_id} exceeded quota of {self.policy.quota_calls} calls "
                    f"per {self.policy.quota_window_s}s for tool {self.policy.name!r}."
                )

        # 4. Effective timeout calculation (bounded by per-tool timeout and remaining budget)
        effective_timeout = self.policy.timeout_s
        if budget is not None:
            effective_timeout = min(effective_timeout, budget.remaining_s)

        # 5. Guarded execution with timeout
        try:
            return await asyncio.wait_for(
                self._tool.invoke(*args, **kwargs),
                timeout=effective_timeout,
            )
        except TimeoutError as exc:
            raise ToolTimeoutError(f"Tool {self.policy.name!r} timed out after {effective_timeout:.1f}s.") from exc


def wrap_tool_registry(
    registry: dict[str, Tool],
    settings: Settings,
    limiter: ToolQuotaLimiter | None = None,
) -> dict[str, Tool]:
    """Wrap each registered tool with its policy enforcement layer and filter disabled tools."""
    enforced: dict[str, Tool] = {}
    quota_limiter = limiter or ToolQuotaLimiter()

    for name, tool in registry.items():
        policy = load_tool_policy(name, settings)
        if policy.enabled:
            enforced[name] = PolicyEnforcedTool(tool=tool, policy=policy, limiter=quota_limiter)
        else:
            logger.info("Tool %r disabled by policy configuration; omitting from active registry", name)

    return enforced
