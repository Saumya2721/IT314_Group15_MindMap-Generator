"""Redis-backed sliding-window rate limiter (S3-BE5 / FR-41).

Replaces in-memory limiters across the backend with a distributed Redis-backed
sliding window implementation so multiple backend replicas and rolling deploys
share quota state without resetting counters.

Provides per-user and per-API quotas for:
1. Generation endpoints ("generation"): default 30 requests/hour
2. Chat send endpoints ("chat"): default 20 requests/minute
3. Memory propose endpoints ("memory_propose"): default 30 requests/minute
4. General / default APIs ("default"): configurable

Gracefully falls back to an in-memory sliding window when Redis is unavailable (NFR-07).
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Any
from uuid import UUID, uuid4

from core.config import settings
from fastapi import HTTPException, Request, status
from services.queue import get_redis_pool

logger = logging.getLogger(__name__)

LUA_SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
local cutoff = now - window

redis.call('zremrangebyscore', key, 0, cutoff)
local current_hits = redis.call('zcard', key)

if current_hits >= limit then
    local oldest = redis.call('zrange', key, 0, 0, 'WITHSCORES')
    local oldest_ts = cutoff
    if oldest and #oldest >= 2 then
        oldest_ts = tonumber(oldest[2])
    end
    local retry_after = math.max(1, math.ceil(window - (now - oldest_ts)))
    return {0, retry_after}
else
    redis.call('zadd', key, now, member)
    redis.call('expire', key, math.ceil(window + 10))
    return {1, 0}
end
"""


class RedisRateLimiter:
    """Distributed Redis-backed sliding-window rate limiter with in-memory fallback."""

    def __init__(self, quotas: dict[str, tuple[int, int]] | None = None):
        """Initialize with per-API quotas: api_name -> (max_calls, window_seconds)."""
        self._quotas: dict[str, tuple[int, int]] = quotas or {
            "generation": (settings.GEN_RATE_LIMIT_PER_HOUR, settings.GEN_RATE_LIMIT_WINDOW_S),
            "export": (settings.GEN_RATE_LIMIT_PER_HOUR, settings.GEN_RATE_LIMIT_WINDOW_S),
            "chat": (settings.CHAT_RATE_LIMIT_PER_MINUTE, settings.CHAT_RATE_LIMIT_WINDOW_S),
            "memory_propose": (
                settings.MEMORY_PROPOSE_RATE_LIMIT_PER_MINUTE,
                settings.MEMORY_PROPOSE_RATE_LIMIT_WINDOW_S,
            ),
            "default": (60, 60),
        }
        self._local_hits: dict[str, deque[float]] = defaultdict(deque)

    def get_quota(self, api: str) -> tuple[int, int]:
        return self._quotas.get(api, self._quotas.get("default", (60, 60)))

    async def is_allowed(
        self,
        key: UUID | str,
        api: str = "default",
        redis: Any = None,
        max_calls: int | None = None,
        window_s: int | None = None,
    ) -> tuple[bool, int]:
        """Check if request is allowed under quota.

        Returns (True, 0) if allowed, or (False, retry_after_seconds) if rejected.
        """
        quota_calls, quota_window = self.get_quota(api)
        limit = max_calls if max_calls is not None else quota_calls
        window = window_s if window_s is not None else quota_window
        key_str = str(key)

        # 1. Try Redis sorted set sliding window (Atomic Lua execution)
        if redis is not None:
            try:
                redis_key = f"rate_limit:{api}:{key_str}"
                now = time.time()
                member = f"{now}-{uuid4().hex}"

                res = await redis.eval(
                    LUA_SLIDING_WINDOW_SCRIPT,
                    1,
                    redis_key,
                    str(now),
                    str(window),
                    str(limit),
                    member,
                )
                allowed = bool(res[0] == 1)
                retry_after = int(res[1])
                return allowed, retry_after
            except Exception as exc:
                logger.warning("Redis rate limiter unavailable for %s", api, exc_info=True)
                raise HTTPException(503, "Rate limiting is temporarily unavailable.") from exc

        # 2. In-memory sliding window fallback (NFR-07)
        now_mono = time.monotonic()
        mem_key = f"{api}:{key_str}"
        dq = self._local_hits[mem_key]

        while dq and (now_mono - dq[0]) > window:
            dq.popleft()

        if len(dq) >= limit:
            oldest_mono = dq[0] if dq else now_mono
            retry_after = max(1, int(window - (now_mono - oldest_mono)))
            return False, retry_after

        dq.append(now_mono)
        return True, 0

    async def check(
        self,
        key: UUID | str,
        api: str = "default",
        redis: Any = None,
        max_calls: int | None = None,
        window_s: int | None = None,
    ) -> None:
        """Enforce rate limit, raising HTTP 429 Too Many Requests if quota is exceeded."""
        allowed, retry_after = await self.is_allowed(
            key=key,
            api=api,
            redis=redis,
            max_calls=max_calls,
            window_s=window_s,
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded for {api}. Please wait {retry_after}s and try again.",
                headers={"Retry-After": str(retry_after)},
            )

    def check_in_memory(
        self,
        key: UUID | str,
        api: str = "default",
        max_calls: int | None = None,
        window_s: int | None = None,
    ) -> None:
        """Synchronous check using strictly in-memory sliding window."""
        quota_calls, quota_window = self.get_quota(api)
        limit = max_calls if max_calls is not None else quota_calls
        window = window_s if window_s is not None else quota_window
        key_str = str(key)

        now_mono = time.monotonic()
        mem_key = f"{api}:{key_str}"
        dq = self._local_hits[mem_key]

        while dq and (now_mono - dq[0]) > window:
            dq.popleft()

        if len(dq) >= limit:
            oldest_mono = dq[0] if dq else now_mono
            retry_after = max(1, int(window - (now_mono - oldest_mono)))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded for {api}. Please wait {retry_after}s and try again.",
                headers={"Retry-After": str(retry_after)},
            )

        dq.append(now_mono)


# Global rate limiter instance
rate_limiter = RedisRateLimiter()


# ---------------------------------------------------------------------------
# FastAPI Dependency Helpers
# ---------------------------------------------------------------------------


def get_rate_limiter() -> RedisRateLimiter:
    return rate_limiter


def extract_client_key(request: Request) -> str:
    """Extract client key for rate limiting: stable user UUID if authenticated, or IP if anonymous (AUD-04)."""
    if not request:
        return "ip:unknown"

    # 1. State user (set by auth dependencies or get_current_user)
    user = getattr(request.state, "user", None)
    if user and hasattr(user, "id"):
        return f"user:{user.id}"

    # 3. Client IP fallback for unauthenticated calls
    if request.client and request.client.host:
        return f"ip:{request.client.host}"

    return "ip:unknown"


async def check_chat_rate_limit(request: Request) -> None:
    """Enforce per-user / per-client rate limit on chat message send (S3-BE5 / FR-41)."""
    redis_pool = get_redis_pool(request)
    client_key = extract_client_key(request)
    await rate_limiter.check(client_key, api="chat", redis=redis_pool)


async def check_memory_propose_rate_limit(request: Request) -> None:
    """Enforce per-user rate limit on memory propose worker endpoint (S3-BE5 / FR-41)."""
    redis_pool = get_redis_pool(request)
    client_key = extract_client_key(request)
    await rate_limiter.check(client_key, api="memory_propose", redis=redis_pool)
