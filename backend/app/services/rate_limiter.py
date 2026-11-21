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

import hashlib
import logging
import time
from collections import defaultdict, deque
from typing import Any
from uuid import UUID, uuid4

from core.config import settings
from fastapi import HTTPException, Request, status
from services.queue import get_redis_pool

logger = logging.getLogger(__name__)


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

        # 1. Try Redis sorted set sliding window
        if redis is not None:
            try:
                redis_key = f"rate_limit:{api}:{key_str}"
                now = time.time()
                cutoff = now - window

                pipe = redis.pipeline()
                pipe.zremrangebyscore(redis_key, 0, cutoff)
                pipe.zcard(redis_key)
                pipe.zrange(redis_key, 0, 0, withscores=True)
                results = await pipe.execute()

                current_hits = int(results[1]) if len(results) > 1 and results[1] is not None else 0
                oldest_entry = results[2] if len(results) > 2 and results[2] else []

                if current_hits >= limit:
                    # Calculate retry after based on oldest entry in window
                    oldest_ts = oldest_entry[0][1] if oldest_entry else cutoff
                    retry_after = max(1, int(window - (now - float(oldest_ts))))
                    return False, retry_after

                # Under limit: add current call and reset expiration
                member = f"{now}-{uuid4().hex}"
                pipe = redis.pipeline()
                pipe.zadd(redis_key, {member: now})
                pipe.expire(redis_key, window + 10)
                await pipe.execute()
                return True, 0
            except Exception as exc:
                logger.warning(
                    "Redis rate limiter failed for %s:%s, using in-memory fallback: %s",
                    api,
                    key_str,
                    exc,
                )

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
    """Extract client key for rate limiting: user id if authenticated, token hash, or IP."""
    if not request:
        return "ip:unknown"

    # 1. Bearer token
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        return hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]

    # 2. Cookie access token
    cookie_token = request.cookies.get("access_token")
    if cookie_token:
        return hashlib.sha256(cookie_token.encode("utf-8")).hexdigest()[:32]

    # 3. Client IP fallback
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
