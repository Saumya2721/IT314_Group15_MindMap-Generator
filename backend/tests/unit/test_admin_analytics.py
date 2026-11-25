"""Unit tests for Admin Aggregate Analytics API (Issue #167 / S3-BE3).

Covers:
1. GET /api/admin/stats & /admin/stats: Successful analytics response.
2. Correct aggregate metrics:
   - User metrics (total_users, active_users_last_7_days, active_users_last_30_days).
   - Map generation metrics (maps_generated_last_7_days, maps_generated_last_30_days).
   - Job failure metrics (failure_rate, error_categories breakdown).
   - Performance metrics (avg_generation_latency, avg_chat_turn_latency).
3. Correct 7-day and 30-day distinct calculations.
4. Job failure rate calculations (normal, zero division, precision).
5. Error category breakdown (multiple categories, empty categories).
6. Average generation and chat turn latency metrics.
7. Empty database / no-data scenarios (graceful fallback to 0 / 0.0 / {}).
8. Database query failures (HTTP 500 on query error, HTTP 503 on uninitialized pool).
9. Unauthorized access (HTTP 401 on missing or invalid token).
10. Forbidden access (HTTP 403 for regular user, other non-admin roles, or suspended admin).
11. Privacy and Security: Strict verification that raw content tables
    (mind_maps, nodes, edges, chat_messages, agent_memories) are NEVER queried.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@asynccontextmanager
async def _noop_lifespan(app):
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_user(user_id: str = "aaa-bbb-ccc", email: str = "admin@example.com") -> SimpleNamespace:
    return SimpleNamespace(id=user_id, email=email)


def _supabase_profile_response(role: str = "admin", suspended: bool = False) -> MagicMock:
    mock_resp = MagicMock()
    data: dict[str, Any] = {"role": role}
    if suspended:
        data["suspended_at"] = "2026-11-20T10:00:00Z"
    else:
        data["suspended_at"] = None
    mock_resp.data = [data]
    return mock_resp


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_infrastructure():
    with (
        patch("core.supabase.is_supabase_configured", return_value=True),
        patch("core.supabase.SUPABASE_URL", "https://fake.supabase.co"),
        patch("core.supabase.ANON_KEY", "fake-anon-key"),
        patch("core.supabase.SERVICE_KEY", "fake-service-key"),
        patch("main.create_redis_pool", AsyncMock(return_value=None)),
        patch("main.create_db_pool", AsyncMock(return_value=None)),
    ):
        yield


@pytest.fixture()
def app() -> FastAPI:
    from main import app as real_app

    real_app.router.lifespan_context = _noop_lifespan
    return real_app


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _patch_admin_auth():
    class _Ctx:
        def __enter__(self_ctx):
            self_ctx.p1 = patch("core.supabase.anon_client")
            self_ctx.p2 = patch("core.supabase.admin")
            mock_anon = self_ctx.p1.__enter__()
            mock_admin = self_ctx.p2.__enter__()

            mock_client = MagicMock()
            mock_client.auth.get_user.return_value = SimpleNamespace(
                user=_make_fake_user("admin-123", "admin@example.com")
            )
            mock_anon.return_value = mock_client
            mock_admin.table.return_value.select.return_value.eq.return_value.execute.return_value = (
                _supabase_profile_response("admin")
            )
            return self_ctx

        def __exit__(self_ctx, *args):
            self_ctx.p2.__exit__(*args)
            self_ctx.p1.__exit__(*args)

    return _Ctx()


# ---------------------------------------------------------------------------
# Test Suite: Admin Aggregate Analytics
# ---------------------------------------------------------------------------


class TestAdminAnalyticsSuccess:
    """Test successful retrieval of aggregate operational metrics."""

    def test_stats_success_structure_and_values(self, app: FastAPI, client: TestClient):
        """GET /api/admin/stats returns all required user, map, failure, and latency metrics."""
        mock_pool = MagicMock()
        mock_pool.execute = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_pool

        executed_queries: list[str] = []

        async def fake_fetchrow(query: str, *args):
            executed_queries.append(query)
            if "admin_active_users" in query:
                return {
                    "total_users": 150,
                    "active_users_last_7_days": 25,
                    "active_users_last_30_days": 80,
                }
            if "admin_generation_counts" in query:
                return {
                    "total_generations": 320,
                    "generations_last_7_days": 45,
                    "generations_last_30_days": 180,
                    "maps_generated_last_7_days": 45,
                    "maps_generated_last_30_days": 180,
                }
            if "admin_generation_failure_rates" in query:
                return {
                    "failed_jobs": 12,
                    "completed_jobs": 288,
                    "failure_rate": 0.04,
                }
            if "admin_performance_latencies" in query:
                return {
                    "avg_generation_latency": 4.35,
                    "avg_chat_turn_latency": 1.12,
                }
            return None

        async def fake_fetch(query: str, *args):
            executed_queries.append(query)
            if "admin_job_error_categories" in query:
                return [
                    {"category": "rate_limit", "count": 6},
                    {"category": "timeout", "count": 4},
                    {"category": "validation", "count": 2},
                ]
            return []

        mock_pool.fetchrow = AsyncMock(side_effect=fake_fetchrow)
        mock_pool.fetch = AsyncMock(side_effect=fake_fetch)
        app.state.pool = mock_pool

        with _patch_admin_auth():
            resp = client.get(
                "/api/admin/stats",
                headers={"Authorization": "Bearer admin-token"},
            )
            assert resp.status_code == 200
            data = resp.json()

            # 1. User Metrics
            assert "users" in data
            assert data["users"]["total_users"] == 150
            assert data["users"]["active_users_last_7_days"] == 25
            assert data["users"]["active_users_last_30_days"] == 80

            # 2. Map Generation Metrics
            assert "map_generation" in data
            assert data["map_generation"]["maps_generated_last_7_days"] == 45
            assert data["map_generation"]["maps_generated_last_30_days"] == 180
            assert data["map_generation"]["total_generations"] == 320

            # 3. Job Failure Metrics
            assert "job_failures" in data
            assert data["job_failures"]["failure_rate"] == 0.04
            assert data["job_failures"]["failed_jobs"] == 12
            assert data["job_failures"]["completed_jobs"] == 288
            assert data["job_failures"]["error_categories"] == {
                "rate_limit": 6,
                "timeout": 4,
                "validation": 2,
            }

            # 4. Performance Metrics
            assert "performance" in data
            assert data["performance"]["avg_generation_latency"] == 4.35
            assert data["performance"]["avg_chat_turn_latency"] == 1.12

            # Top-level direct access aliases
            assert data["total_users"] == 150
            assert data["active_users_last_7_days"] == 25
            assert data["active_users_last_30_days"] == 80
            assert data["maps_generated_last_7_days"] == 45
            assert data["maps_generated_last_30_days"] == 180
            assert data["failure_rate"] == 0.04
            assert data["error_categories"] == {
                "rate_limit": 6,
                "timeout": 4,
                "validation": 2,
            }
            assert data["avg_generation_latency"] == 4.35
            assert data["avg_chat_turn_latency"] == 1.12

    def test_stats_mounted_at_both_prefixes(self, app: FastAPI, client: TestClient):
        """Verify GET /admin/stats works without /api prefix as well."""
        mock_pool = MagicMock()
        mock_pool.execute = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_pool
        mock_pool.fetchrow = AsyncMock(return_value={})
        mock_pool.fetch = AsyncMock(return_value=[])
        app.state.pool = mock_pool

        with _patch_admin_auth():
            resp1 = client.get("/api/admin/stats", headers={"Authorization": "Bearer admin-token"})
            resp2 = client.get("/admin/stats", headers={"Authorization": "Bearer admin-token"})
            assert resp1.status_code == 200
            assert resp2.status_code == 200
            assert resp1.json() == resp2.json()


class TestAdminAnalyticsCalculations:
    """Test edge cases in aggregate metrics calculations."""

    def test_empty_database_no_data(self, app: FastAPI, client: TestClient):
        """When views return None or empty results, endpoint returns zeros and empty error breakdown."""
        mock_pool = MagicMock()
        mock_pool.execute = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_pool
        mock_pool.fetchrow = AsyncMock(return_value=None)
        mock_pool.fetch = AsyncMock(return_value=[])
        app.state.pool = mock_pool

        with _patch_admin_auth():
            resp = client.get("/api/admin/stats", headers={"Authorization": "Bearer admin-token"})
            assert resp.status_code == 200
            data = resp.json()

            assert data["users"]["total_users"] == 0
            assert data["users"]["active_users_last_7_days"] == 0
            assert data["users"]["active_users_last_30_days"] == 0

            assert data["map_generation"]["maps_generated_last_7_days"] == 0
            assert data["map_generation"]["maps_generated_last_30_days"] == 0
            assert data["map_generation"]["total_generations"] == 0

            assert data["job_failures"]["failure_rate"] == 0.0
            assert data["job_failures"]["failed_jobs"] == 0
            assert data["job_failures"]["completed_jobs"] == 0
            assert data["job_failures"]["error_categories"] == {}

            assert data["performance"]["avg_generation_latency"] == 0.0
            assert data["performance"]["avg_chat_turn_latency"] == 0.0

    def test_distinct_7_and_30_day_metrics(self, app: FastAPI, client: TestClient):
        """Verify 7-day and 30-day calculations can differ appropriately."""
        mock_pool = MagicMock()
        mock_pool.execute = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_pool

        async def fake_fetchrow(query: str, *args):
            if "admin_active_users" in query:
                return {
                    "total_users": 500,
                    "active_users_last_7_days": 42,
                    "active_users_last_30_days": 210,
                }
            if "admin_generation_counts" in query:
                return {
                    "total_generations": 1000,
                    "generations_last_7_days": 120,
                    "generations_last_30_days": 650,
                    "maps_generated_last_7_days": 120,
                    "maps_generated_last_30_days": 650,
                }
            return {}

        mock_pool.fetchrow = AsyncMock(side_effect=fake_fetchrow)
        mock_pool.fetch = AsyncMock(return_value=[])
        app.state.pool = mock_pool

        with _patch_admin_auth():
            resp = client.get("/api/admin/stats", headers={"Authorization": "Bearer admin-token"})
            assert resp.status_code == 200
            data = resp.json()

            assert data["users"]["active_users_last_7_days"] == 42
            assert data["users"]["active_users_last_30_days"] == 210
            assert data["map_generation"]["maps_generated_last_7_days"] == 120
            assert data["map_generation"]["maps_generated_last_30_days"] == 650

    def test_failure_rate_zero_and_custom(self, app: FastAPI, client: TestClient):
        """Verify job failure rate handles zero completed/failed jobs and non-zero rates."""
        mock_pool = MagicMock()
        mock_pool.execute = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_pool

        async def fake_fetchrow(query: str, *args):
            if "admin_generation_failure_rates" in query:
                return {
                    "failed_jobs": 0,
                    "completed_jobs": 50,
                    "failure_rate": 0.0,
                }
            return {}

        mock_pool.fetchrow = AsyncMock(side_effect=fake_fetchrow)
        mock_pool.fetch = AsyncMock(return_value=[])
        app.state.pool = mock_pool

        with _patch_admin_auth():
            resp = client.get("/api/admin/stats", headers={"Authorization": "Bearer admin-token"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["job_failures"]["failure_rate"] == 0.0
            assert data["job_failures"]["failed_jobs"] == 0
            assert data["job_failures"]["completed_jobs"] == 50

    def test_error_categories_breakdown(self, app: FastAPI, client: TestClient):
        """Verify error category breakdown correctly maps diverse error buckets."""
        mock_pool = MagicMock()
        mock_pool.execute = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_pool
        mock_pool.fetchrow = AsyncMock(return_value={})

        async def fake_fetch(query: str, *args):
            if "admin_job_error_categories" in query:
                return [
                    {"category": "rate_limit", "count": 15},
                    {"category": "timeout", "count": 8},
                    {"category": "auth", "count": 3},
                    {"category": "provider_error", "count": 7},
                    {"category": "other", "count": 2},
                ]
            return []

        mock_pool.fetch = AsyncMock(side_effect=fake_fetch)
        app.state.pool = mock_pool

        with _patch_admin_auth():
            resp = client.get("/api/admin/stats", headers={"Authorization": "Bearer admin-token"})
            assert resp.status_code == 200
            data = resp.json()
            cats = data["job_failures"]["error_categories"]
            assert cats["rate_limit"] == 15
            assert cats["timeout"] == 8
            assert cats["auth"] == 3
            assert cats["provider_error"] == 7
            assert cats["other"] == 2


class TestAdminAnalyticsSecurityAndPrivacy:
    """Verify that raw content tables are never queried and privacy is preserved."""

    def test_strict_aggregate_views_only_no_content_tables(self, app: FastAPI, client: TestClient):
        """Verify that the analytics endpoint NEVER queries raw content tables.

        Content tables forbidden for admin inspection:
        - public.mind_maps
        - public.nodes
        - public.edges
        - public.chat_messages
        - public.agent_memories
        """
        mock_pool = MagicMock()
        mock_pool.execute = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_pool
        executed_queries: list[str] = []

        async def record_query(query: str, *args):
            executed_queries.append(query)
            return {}

        async def record_queries(query: str, *args):
            executed_queries.append(query)
            return []

        mock_pool.fetchrow = AsyncMock(side_effect=record_query)
        mock_pool.fetch = AsyncMock(side_effect=record_queries)
        app.state.pool = mock_pool

        with _patch_admin_auth():
            resp = client.get("/api/admin/stats", headers={"Authorization": "Bearer admin-token"})
            assert resp.status_code == 200

        forbidden_tables = [
            "public.mind_maps",
            "public.nodes",
            "public.edges",
            "public.chat_messages",
            "public.agent_memories",
            "from mind_maps",
            "from nodes",
            "from edges",
            "from chat_messages",
            "from agent_memories",
        ]

        assert len(executed_queries) > 0, "No queries were executed"
        for q in executed_queries:
            lower_q = q.lower()
            # Assert all queries target public.admin_* views
            assert "public.admin_" in lower_q or "admin_" in lower_q, f"Query does not target admin views: {q}"
            # Assert no forbidden raw content tables are queried
            for forbidden in forbidden_tables:
                assert forbidden not in lower_q, f"Forbidden content table '{forbidden}' detected in query: {q}"

    def test_no_private_user_content_in_response(self, app: FastAPI, client: TestClient):
        """Verify response contains only aggregate numbers and no sensitive text."""
        mock_pool = MagicMock()
        mock_pool.execute = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_pool
        mock_pool.fetchrow = AsyncMock(return_value={"total_users": 10})
        mock_pool.fetch = AsyncMock(return_value=[{"category": "rate_limit", "count": 1}])
        app.state.pool = mock_pool

        with _patch_admin_auth():
            resp = client.get("/api/admin/stats", headers={"Authorization": "Bearer admin-token"})
            assert resp.status_code == 200
            data = resp.json()

            # Ensure typical user content keys are not present
            sensitive_keys = {"email", "title", "label", "prompt", "content", "message", "password"}
            for key in sensitive_keys:
                assert key not in data
                assert key not in data.get("users", {})
                assert key not in data.get("map_generation", {})


class TestAdminAnalyticsAuthorization:
    """Test authorization requirements on GET /admin/stats."""

    def test_stats_no_token_returns_401(self, client: TestClient):
        """Requests without an Authorization header return HTTP 401."""
        resp = client.get("/api/admin/stats")
        assert resp.status_code == 401

    def test_stats_invalid_token_returns_401(self, client: TestClient):
        """Requests with an invalid token return HTTP 401."""
        with patch("core.supabase.anon_client") as mock_anon:
            mock_client = MagicMock()
            mock_client.auth.get_user.return_value = SimpleNamespace(user=None)
            mock_anon.return_value = mock_client

            resp = client.get(
                "/api/admin/stats",
                headers={"Authorization": "Bearer invalid-token"},
            )
            assert resp.status_code == 401

    def test_stats_forbidden_for_regular_user(self, client: TestClient):
        """Users with role='user' return HTTP 403 Forbidden."""
        with (
            patch("core.supabase.anon_client") as mock_anon,
            patch("core.supabase.admin") as mock_admin,
        ):
            mock_client = MagicMock()
            mock_client.auth.get_user.return_value = SimpleNamespace(user=_make_fake_user("user-1", "user@example.com"))
            mock_anon.return_value = mock_client
            mock_admin.table.return_value.select.return_value.eq.return_value.execute.return_value = (
                _supabase_profile_response("user")
            )

            resp = client.get(
                "/api/admin/stats",
                headers={"Authorization": "Bearer user-token"},
            )
            assert resp.status_code == 403

    def test_stats_forbidden_for_suspended_admin(self, client: TestClient):
        """Admins who have been suspended return HTTP 403 Forbidden."""
        with (
            patch("core.supabase.anon_client") as mock_anon,
            patch("core.supabase.admin") as mock_admin,
        ):
            mock_client = MagicMock()
            mock_client.auth.get_user.return_value = SimpleNamespace(
                user=_make_fake_user("admin-1", "admin@example.com")
            )
            mock_anon.return_value = mock_client
            mock_admin.table.return_value.select.return_value.eq.return_value.execute.return_value = (
                _supabase_profile_response("admin", suspended=True)
            )

            resp = client.get(
                "/api/admin/stats",
                headers={"Authorization": "Bearer suspended-admin-token"},
            )
            assert resp.status_code == 403


class TestAdminAnalyticsFailureModes:
    """Test failure and error handling in admin analytics endpoint."""

    def test_db_pool_uninitialized_returns_503(self, app: FastAPI, client: TestClient):
        """When app.state.pool is None, returns HTTP 503."""
        app.state.pool = None

        with _patch_admin_auth():
            resp = client.get(
                "/api/admin/stats",
                headers={"Authorization": "Bearer admin-token"},
            )
            assert resp.status_code == 503
            assert "Database pool not initialized" in resp.json()["detail"]

    def test_db_query_error_returns_500(self, app: FastAPI, client: TestClient):
        """When view query fails, endpoint catches error and returns HTTP 500."""
        mock_pool = MagicMock()
        mock_pool.execute = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_pool
        mock_pool.fetchrow = AsyncMock(side_effect=Exception("Database connection timeout"))
        mock_pool.fetch = AsyncMock(return_value=[])
        app.state.pool = mock_pool

        with _patch_admin_auth():
            resp = client.get(
                "/api/admin/stats",
                headers={"Authorization": "Bearer admin-token"},
            )
            assert resp.status_code == 500
            assert "Failed to retrieve aggregate operational analytics" in resp.json()["detail"]
