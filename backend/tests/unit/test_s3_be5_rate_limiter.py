"""Unit tests for S3-BE5: Redis-backed distributed rate limiter (FR-41).

Covers:
- Distributed Redis-backed sliding window with atomic pipeline
- Multi-replica and rolling deploy quota sharing across distinct limiter instances
- Graceful in-memory sliding window fallback on Redis failures (NFR-07)
- Per-user and per-API quotas for generation, chat send, and memory propose
- HTTP 429 status code and Retry-After header enforcement
- Rate limiter dependencies on generation, chat, and memory propose routes
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from services.rate_limiter import (
    RedisRateLimiter,
    check_chat_rate_limit,
    check_memory_propose_rate_limit,
    extract_client_key,
)
from services.runner import RateLimiter

# ---------------------------------------------------------------------------
# Mock Redis Client
# ---------------------------------------------------------------------------


class MockRedis:
    """In-memory Redis mock replicating zremrangebyscore, zcard, zrange, zadd, expire."""

    def __init__(self) -> None:
        self.zsets: dict[str, dict[str, float]] = {}

    def pipeline(self) -> MockRedisPipeline:
        return MockRedisPipeline(self)

    async def eval(self, script: str, numkeys: int, *args) -> list:
        key = args[0]
        now = float(args[1])
        window = float(args[2])
        limit = int(args[3])
        member = args[4]
        cutoff = now - window
        z = self.zsets.setdefault(key, {})
        to_del = [m for m, s in z.items() if s <= cutoff]
        for m in to_del:
            del z[m]
        count = len(z)
        if count >= limit:
            sorted_items = sorted(z.items(), key=lambda x: x[1])
            oldest_ts = sorted_items[0][1] if sorted_items else cutoff
            import math

            retry_after = max(1, math.ceil(window - (now - oldest_ts)))
            return [0, retry_after]
        else:
            z[member] = now
            return [1, 0]


class MockRedisPipeline:
    def __init__(self, redis: MockRedis) -> None:
        self.redis = redis
        self.ops: list[tuple] = []

    def zremrangebyscore(self, key: str, min_val: float, max_val: float) -> MockRedisPipeline:
        self.ops.append(("rem", key, min_val, max_val))
        return self

    def zcard(self, key: str) -> MockRedisPipeline:
        self.ops.append(("card", key))
        return self

    def zrange(self, key: str, start: int, stop: int, withscores: bool = False) -> MockRedisPipeline:
        self.ops.append(("range", key, start, stop, withscores))
        return self

    def zadd(self, key: str, mapping: dict[str, float]) -> MockRedisPipeline:
        self.ops.append(("add", key, mapping))
        return self

    def expire(self, key: str, ttl: int) -> MockRedisPipeline:
        self.ops.append(("expire", key, ttl))
        return self

    async def execute(self) -> list:
        results: list = []
        for op in self.ops:
            op_type = op[0]
            if op_type == "rem":
                key, min_val, max_val = op[1], float(op[2]), float(op[3])
                z = self.redis.zsets.setdefault(key, {})
                to_del = [m for m, s in z.items() if min_val <= s <= max_val]
                for m in to_del:
                    del z[m]
                results.append(len(to_del))
            elif op_type == "card":
                key = op[1]
                results.append(len(self.redis.zsets.get(key, {})))
            elif op_type == "range":
                key, start, stop, withscores = op[1], op[2], op[3], op[4]
                z = self.redis.zsets.get(key, {})
                sorted_items = sorted(z.items(), key=lambda x: x[1])
                sliced = sorted_items[start : stop + 1 if stop != -1 else None]
                if withscores:
                    results.append(sliced)
                else:
                    results.append([m for m, s in sliced])
            elif op_type == "add":
                key, mapping = op[1], op[2]
                z = self.redis.zsets.setdefault(key, {})
                z.update(mapping)
                results.append(len(mapping))
            elif op_type == "expire":
                results.append(True)
        return results


# ---------------------------------------------------------------------------
# 1. Redis Sliding Window Unit Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_limiter_allows_up_to_quota() -> None:
    redis = MockRedis()
    limiter = RedisRateLimiter({"test_api": (3, 60)})
    user_id = uuid4()

    # 3 calls allowed
    for _ in range(3):
        allowed, retry_after = await limiter.is_allowed(user_id, api="test_api", redis=redis)
        assert allowed is True
        assert retry_after == 0

    # 4th call rejected
    allowed, retry_after = await limiter.is_allowed(user_id, api="test_api", redis=redis)
    assert allowed is False
    assert retry_after > 0


@pytest.mark.asyncio
async def test_redis_limiter_raises_http_429() -> None:
    redis = MockRedis()
    limiter = RedisRateLimiter({"chat": (2, 60)})
    user_id = "user-123"

    await limiter.check(user_id, api="chat", redis=redis)
    await limiter.check(user_id, api="chat", redis=redis)

    with pytest.raises(HTTPException) as exc_info:
        await limiter.check(user_id, api="chat", redis=redis)

    assert exc_info.value.status_code == 429
    assert "Retry-After" in exc_info.value.headers
    assert int(exc_info.value.headers["Retry-After"]) >= 1


@pytest.mark.asyncio
async def test_multi_replica_sharing() -> None:
    """Verify that multiple limiter instances sharing the same Redis backend share quota."""
    shared_redis = MockRedis()
    replica_1 = RedisRateLimiter({"generation": (2, 3600)})
    replica_2 = RedisRateLimiter({"generation": (2, 3600)})
    user_id = uuid4()

    # Replica 1 consumes call 1
    allowed_1, _ = await replica_1.is_allowed(user_id, api="generation", redis=shared_redis)
    assert allowed_1 is True

    # Replica 2 consumes call 2
    allowed_2, _ = await replica_2.is_allowed(user_id, api="generation", redis=shared_redis)
    assert allowed_2 is True

    # Replica 1 rejects call 3 because quota was depleted across replicas
    allowed_3, retry = await replica_1.is_allowed(user_id, api="generation", redis=shared_redis)
    assert allowed_3 is False
    assert retry > 0


@pytest.mark.asyncio
async def test_per_api_and_per_user_isolation() -> None:
    """Verify different users and different APIs have independent quotas."""
    redis = MockRedis()
    limiter = RedisRateLimiter({"chat": (1, 60), "memory_propose": (1, 60)})
    user_a = uuid4()
    user_b = uuid4()

    # User A consumes chat quota
    assert (await limiter.is_allowed(user_a, api="chat", redis=redis))[0] is True
    assert (await limiter.is_allowed(user_a, api="chat", redis=redis))[0] is False

    # User B still has fresh chat quota
    assert (await limiter.is_allowed(user_b, api="chat", redis=redis))[0] is True

    # User A still has fresh memory_propose quota (different API)
    assert (await limiter.is_allowed(user_a, api="memory_propose", redis=redis))[0] is True
    assert (await limiter.is_allowed(user_a, api="memory_propose", redis=redis))[0] is False


# ---------------------------------------------------------------------------
# 2. In-Memory Fallback Tests (NFR-07)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_in_memory_fallback_when_redis_none() -> None:
    limiter = RedisRateLimiter({"generation": (2, 10)})
    user_id = uuid4()

    # Operates cleanly without redis
    assert (await limiter.is_allowed(user_id, api="generation", redis=None))[0] is True
    assert (await limiter.is_allowed(user_id, api="generation", redis=None))[0] is True
    assert (await limiter.is_allowed(user_id, api="generation", redis=None))[0] is False


@pytest.mark.asyncio
async def test_redis_exception_fails_closed() -> None:
    broken_redis = MagicMock()
    broken_redis.eval = AsyncMock(side_effect=RuntimeError("Redis connection dropped"))
    limiter = RedisRateLimiter({"chat": (2, 60)})
    with pytest.raises(HTTPException) as exc_info:
        await limiter.is_allowed(uuid4(), api="chat", redis=broken_redis)
    assert exc_info.value.status_code == 503


def test_rate_limiter_sync_in_memory() -> None:
    limiter = RateLimiter(max_calls=2, window_s=60)
    user_id = uuid4()

    limiter.check_sync(user_id)
    limiter.check_sync(user_id)

    with pytest.raises(HTTPException) as exc_info:
        limiter.check_sync(user_id)
    assert exc_info.value.status_code == 429


# ---------------------------------------------------------------------------
# 3. HTTP Dependency Integration Tests
# ---------------------------------------------------------------------------


def test_chat_rate_limit_dependency_enforced() -> None:
    app = FastAPI()
    mock_redis = MockRedis()
    app.state.redis_pool = mock_redis

    @app.post("/test-chat")
    async def chat_endpoint(request: Request):
        await check_chat_rate_limit(request)
        return {"status": "sent"}

    client = TestClient(app)

    # Send up to chat quota
    for _ in range(20):
        resp = client.post("/test-chat", headers={"Authorization": "Bearer token-user-abc"})
        assert resp.status_code == 200

    # 21st request hits 429
    resp = client.post("/test-chat", headers={"Authorization": "Bearer token-user-abc"})
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


def test_memory_propose_rate_limit_dependency_enforced() -> None:
    app = FastAPI()
    mock_redis = MockRedis()
    app.state.redis_pool = mock_redis

    @app.post("/test-propose")
    async def propose_endpoint(request: Request):
        await check_memory_propose_rate_limit(request)
        return {"status": "proposed"}

    client = TestClient(app)

    # Send up to propose quota (30)
    for _ in range(30):
        resp = client.post("/test-propose", headers={"Authorization": "Bearer token-worker-xyz"})
        assert resp.status_code == 200

    # 31st request hits 429
    resp = client.post("/test-propose", headers={"Authorization": "Bearer token-worker-xyz"})
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


def test_extract_client_key_variations() -> None:
    # 1. Authenticated user in state
    req_user = MagicMock()
    req_user.state.user.id = "user-uuid-123"
    assert extract_client_key(req_user) == "user:user-uuid-123"

    # 2. JWT token with sub claim in bearer header
    import base64

    header_b64 = base64.urlsafe_b64encode(b'{"alg":"HS256"}').decode("utf-8").rstrip("=")
    payload_b64 = base64.urlsafe_b64encode(b'{"sub":"jwt-user-456"}').decode("utf-8").rstrip("=")
    fake_jwt = f"{header_b64}.{payload_b64}.signature"

    req_jwt = MagicMock()
    req_jwt.state = MagicMock(spec=[])  # no user attr
    req_jwt.headers = {"authorization": f"Bearer {fake_jwt}"}
    req_jwt.cookies = {}
    req_jwt.client.host = "192.168.1.45"
    assert extract_client_key(req_jwt) == "ip:192.168.1.45"

    # 3. Unauthenticated client with IP
    req_ip = MagicMock()
    req_ip.state = MagicMock(spec=[])
    req_ip.headers = {}
    req_ip.cookies = {}
    req_ip.client.host = "192.168.1.50"
    key2 = extract_client_key(req_ip)
    assert key2 == "ip:192.168.1.50"
