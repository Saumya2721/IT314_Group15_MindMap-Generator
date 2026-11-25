"""
Redis result cache for research agent queries (S3-A2).

Guarantees:
  - Deterministic key generation: research_cache:{tool_name}:{sha256(normalized_query)}
  - TTL of exactly 3600 seconds (1 hour)
  - Complete error shielding: Redis timeouts or connection failures log warnings and degrade
    gracefully to direct tool invocation (NFR-07: Graceful Degradation).
  - Clean in-memory fallback for offline test suites and local development.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from research.models import ResearchSource

logger = logging.getLogger(__name__)

DEFAULT_CACHE_TTL_S = 3600  # 1 hour

# Public research tools share deterministic global cache keys (NEW-09, AUD-08).
PUBLIC_CACHEABLE_TOOLS: frozenset[str] = frozenset({"wikipedia", "scholar", "arxiv", "arxiv_search", "web_search"})


def make_cache_key(tool_name: str, query: str, user_id: str | None = None) -> str | None:
    """Generate deterministic, collision-resistant Redis cache key for a tool and query.

    Public tools (wikipedia, scholar, arxiv, web_search) share global cache keys.
    Private or non-whitelisted tools (such as web_page_reader) scope cache keys per user
    to prevent cross-user data leakage (NEW-09, AUD-08).
    """
    norm_tool = tool_name.strip().lower()
    if norm_tool not in PUBLIC_CACHEABLE_TOOLS and not user_id:
        return None
    norm_query = " ".join(query.strip().lower().split()) if norm_tool in PUBLIC_CACHEABLE_TOOLS else query.strip()
    digest = hashlib.sha256(norm_query.encode("utf-8")).hexdigest()
    if norm_tool in PUBLIC_CACHEABLE_TOOLS:
        return f"research_cache:{norm_tool}:{digest}"
    uid = str(user_id).strip()
    return f"research_cache:user:{uid}:{norm_tool}:{digest}"


class ResearchCache:
    """Redis-backed research query cache with in-memory fallback."""

    def __init__(self, redis_client: Any = None, ttl_s: int = DEFAULT_CACHE_TTL_S):
        self._redis = redis_client
        self._ttl_s = ttl_s
        self._local_cache: dict[str, tuple[float, str]] = {}  # key -> (expires_at, json_data)

    def set_redis(self, redis_client: Any) -> None:
        """Update active Redis client."""
        self._redis = redis_client

    async def get(self, tool_name: str, query: str, user_id: str | None = None) -> list[ResearchSource] | None:
        """Retrieve cached research sources for a tool query.

        Returns None on cache miss, expired entry, or Redis communication failure.
        """
        key = make_cache_key(tool_name, query, user_id=user_id)
        if key is None:
            return None

        # 1. Try Redis
        if self._redis is not None:
            try:
                raw = await self._redis.get(key)
                if raw is not None:
                    data = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                    items = json.loads(data)
                    if isinstance(items, list):
                        logger.debug("Redis cache HIT for key %s (%d items)", key, len(items))
                        return [ResearchSource.from_dict(it) for it in items]
                logger.debug("Redis cache MISS for key %s", key)
                return None
            except Exception as exc:
                logger.warning("Redis cache read error for key %s: %s (falling back)", key, exc)

        # 2. In-memory fallback
        entry = self._local_cache.get(key)
        if entry:
            expires_at, data = entry
            if time.monotonic() < expires_at:
                try:
                    items = json.loads(data)
                    return [ResearchSource.from_dict(it) for it in items]
                except Exception:
                    pass
            else:
                self._local_cache.pop(key, None)

        return None

    async def set(
        self,
        tool_name: str,
        query: str,
        sources: list[ResearchSource],
        ttl_s: int | None = None,
        user_id: str | None = None,
    ) -> bool:
        """Store research sources in cache with specified TTL (defaults to 3600 seconds).

        Returns True if stored successfully in either Redis or local cache.
        """
        if not sources:
            # Do not cache empty or failed queries to avoid corrupting future rounds
            return False

        key = make_cache_key(tool_name, query, user_id=user_id)
        if key is None:
            return False
        effective_ttl = ttl_s if ttl_s is not None else self._ttl_s

        try:
            payload = json.dumps([s.to_dict() for s in sources])
        except Exception as exc:
            logger.warning("Serialization error while caching results for key %s: %s", key, exc)
            return False

        # 1. Try Redis
        if self._redis is not None:
            try:
                # Support both redis.asyncio (set(..., ex=ttl)) and arq Redis (setex)
                if hasattr(self._redis, "setex"):
                    await self._redis.setex(key, effective_ttl, payload)
                else:
                    await self._redis.set(key, payload, ex=effective_ttl)
                logger.debug("Cached %d sources in Redis with key %s (TTL=%ds)", len(sources), key, effective_ttl)
                return True
            except Exception as exc:
                logger.warning("Redis cache write error for key %s: %s (falling back to in-memory)", key, exc)

        # 2. In-memory fallback
        self._local_cache[key] = (time.monotonic() + effective_ttl, payload)
        return True

    def clear_local(self) -> None:
        """Clear local in-memory cache."""
        self._local_cache.clear()
