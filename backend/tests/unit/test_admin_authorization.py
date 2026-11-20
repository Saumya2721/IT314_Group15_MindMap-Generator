"""Unit tests for admin role-based authorization.

Covers all specified authorization scenarios:
1. No authentication token → 401
2. Invalid/expired token → 401
3. Valid token, regular user role → 403
4. Valid token, non-admin role → 403
5. Valid token, admin role → 200
6. All admin endpoints are protected
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@asynccontextmanager
async def _noop_lifespan(app):
    yield


# ---------------------------------------------------------------------------
# Helpers — fake Supabase responses
# ---------------------------------------------------------------------------


def _make_fake_user(user_id: str = "aaa-bbb-ccc", email: str = "admin@example.com") -> SimpleNamespace:
    return SimpleNamespace(id=user_id, email=email)


def _supabase_profile_response(role: str = "admin") -> MagicMock:
    """Return an object matching ``supabase_admin.table(...).select(...).eq(...).execute()``."""
    mock_resp = MagicMock()
    mock_resp.data = [{"role": role}]
    return mock_resp


def _supabase_empty_profile_response() -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.data = []
    return mock_resp


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_infrastructure():
    """Disable real Supabase, DB pool, and Redis access for all tests in this module."""
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
    """Import the real FastAPI app after patching infrastructure config."""
    from main import app as real_app

    real_app.router.lifespan_context = _noop_lifespan
    return real_app


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. No authentication token → 401
# ---------------------------------------------------------------------------


class TestNoToken:
    """Requests without any auth token must be rejected with 401."""

    def test_admin_dashboard_no_token(self, client: TestClient):
        resp = client.get("/api/admin/dashboard")
        assert resp.status_code == 401

    def test_admin_users_no_token(self, client: TestClient):
        resp = client.get("/api/admin/users")
        assert resp.status_code == 401

    def test_admin_user_detail_no_token(self, client: TestClient):
        resp = client.get("/api/admin/users/00000000-0000-0000-0000-000000000001")
        assert resp.status_code == 401

    def test_admin_audit_log_no_token(self, client: TestClient):
        resp = client.get("/api/admin/audit-log")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 2. Invalid / expired token → 401
# ---------------------------------------------------------------------------


class TestInvalidToken:
    """Requests with an invalid or expired JWT must be rejected with 401."""

    def test_admin_dashboard_invalid_token(self, client: TestClient):
        with patch("core.supabase.anon_client") as mock_anon:
            mock_client = MagicMock()
            mock_client.auth.get_user.return_value = SimpleNamespace(user=None)
            mock_anon.return_value = mock_client

            resp = client.get(
                "/api/admin/dashboard",
                headers={"Authorization": "Bearer invalid-token-xyz"},
            )
            assert resp.status_code == 401

    def test_admin_users_expired_token(self, client: TestClient):
        with patch("core.supabase.anon_client") as mock_anon:
            mock_client = MagicMock()
            mock_client.auth.get_user.side_effect = Exception("Token expired")
            mock_anon.return_value = mock_client

            resp = client.get(
                "/api/admin/users",
                headers={"Authorization": "Bearer expired-token"},
            )
            assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 3. Regular user (role='user') → 403
# ---------------------------------------------------------------------------


class TestRegularUserForbidden:
    """An authenticated regular user must receive 403 on all admin endpoints."""

    def _patch_auth_and_profile(self, role: str = "user"):
        """Context-manager patches for a fully authenticated user with the given role."""

        class _Ctx:
            def __enter__(self_ctx):
                self_ctx.p1 = patch("core.supabase.anon_client")
                self_ctx.p2 = patch("auth.dependencies.supabase_admin")
                mock_anon = self_ctx.p1.__enter__()
                mock_admin = self_ctx.p2.__enter__()

                # Auth succeeds
                mock_client = MagicMock()
                mock_client.auth.get_user.return_value = SimpleNamespace(user=_make_fake_user())
                mock_anon.return_value = mock_client

                # Profile lookup returns the given role
                mock_admin.table.return_value.select.return_value.eq.return_value.execute.return_value = (
                    _supabase_profile_response(role)
                )
                return self_ctx

            def __exit__(self_ctx, *args):
                self_ctx.p1.__exit__(*args)
                self_ctx.p2.__exit__(*args)

        return _Ctx()

    def test_dashboard_forbidden_for_regular_user(self, client: TestClient):
        with self._patch_auth_and_profile("user"):
            resp = client.get(
                "/api/admin/dashboard",
                headers={"Authorization": "Bearer valid-user-token"},
            )
            assert resp.status_code == 403

    def test_users_forbidden_for_regular_user(self, client: TestClient):
        with self._patch_auth_and_profile("user"):
            resp = client.get(
                "/api/admin/users",
                headers={"Authorization": "Bearer valid-user-token"},
            )
            assert resp.status_code == 403

    def test_user_detail_forbidden_for_regular_user(self, client: TestClient):
        with self._patch_auth_and_profile("user"):
            resp = client.get(
                "/api/admin/users/00000000-0000-0000-0000-000000000001",
                headers={"Authorization": "Bearer valid-user-token"},
            )
            assert resp.status_code == 403

    def test_audit_log_forbidden_for_regular_user(self, client: TestClient):
        with self._patch_auth_and_profile("user"):
            resp = client.get(
                "/api/admin/audit-log",
                headers={"Authorization": "Bearer valid-user-token"},
            )
            assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 4. Other non-admin roles → 403
# ---------------------------------------------------------------------------


class TestOtherRolesForbidden:
    """Any role that is not 'admin' must receive 403."""

    @pytest.mark.parametrize("role", ["user", "moderator", "editor", "viewer"])
    def test_non_admin_role_forbidden(self, client: TestClient, role: str):
        with (
            patch("core.supabase.anon_client") as mock_anon,
            patch("auth.dependencies.supabase_admin") as mock_admin,
        ):
            mock_client = MagicMock()
            mock_client.auth.get_user.return_value = SimpleNamespace(user=_make_fake_user())
            mock_anon.return_value = mock_client

            mock_admin.table.return_value.select.return_value.eq.return_value.execute.return_value = (
                _supabase_profile_response(role)
            )

            resp = client.get(
                "/api/admin/dashboard",
                headers={"Authorization": "Bearer valid-token"},
            )
            assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 5. Admin user → success (200)
# ---------------------------------------------------------------------------


class TestAdminAccess:
    """An authenticated admin user must access admin endpoints normally."""

    def _patch_admin_auth(self):
        class _Ctx:
            def __enter__(self_ctx):
                self_ctx.p1 = patch("core.supabase.anon_client")
                self_ctx.p2 = patch("auth.dependencies.supabase_admin")
                mock_anon = self_ctx.p1.__enter__()
                mock_admin_client = self_ctx.p2.__enter__()

                mock_client = MagicMock()
                mock_client.auth.get_user.return_value = SimpleNamespace(user=_make_fake_user())
                mock_anon.return_value = mock_client

                mock_admin_client.table.return_value.select.return_value.eq.return_value.execute.return_value = (
                    _supabase_profile_response("admin")
                )
                return self_ctx

            def __exit__(self_ctx, *args):
                self_ctx.p1.__exit__(*args)
                self_ctx.p2.__exit__(*args)

        return _Ctx()

    def test_dashboard_success_for_admin(self, client: TestClient):
        with self._patch_admin_auth():
            # Mock the DB pool for the dashboard query
            mock_pool = MagicMock()

            async def fake_fetchval(sql, *a, **kw):
                return 42

            mock_pool.fetchval = fake_fetchval

            cast(FastAPI, client.app).state.pool = mock_pool
            resp = client.get(
                "/api/admin/dashboard",
                headers={"Authorization": "Bearer valid-admin-token"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "total_users" in data
            assert "total_maps" in data

    def test_users_list_success_for_admin(self, client: TestClient):
        with self._patch_admin_auth():
            mock_pool = MagicMock()

            async def fake_fetch(sql, *a, **kw):
                return []

            mock_pool.fetch = fake_fetch

            cast(FastAPI, client.app).state.pool = mock_pool
            resp = client.get(
                "/api/admin/users",
                headers={"Authorization": "Bearer valid-admin-token"},
            )
            assert resp.status_code == 200
            assert isinstance(resp.json(), list)

    def test_audit_log_success_for_admin(self, client: TestClient):
        with self._patch_admin_auth():
            mock_pool = MagicMock()

            async def fake_fetch(sql, *a, **kw):
                return []

            mock_pool.fetch = fake_fetch

            cast(FastAPI, client.app).state.pool = mock_pool
            resp = client.get(
                "/api/admin/audit-log",
                headers={"Authorization": "Bearer valid-admin-token"},
            )
            assert resp.status_code == 200
            assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# 6. All admin endpoints are protected (meta-test)
# ---------------------------------------------------------------------------


class TestAllAdminEndpointsProtected:
    """Verify that every route under ``/api/admin/`` rejects unauthenticated requests."""

    def test_every_admin_route_requires_auth(self, app: FastAPI):
        """Walk the app's routes and confirm all /api/admin/* paths return 401
        when called without credentials."""
        test_client = TestClient(app)

        openapi_paths = app.openapi()["paths"]
        admin_routes: list[tuple[str, list[str]]] = []
        for path, path_item in openapi_paths.items():
            if "/admin" in path:
                methods = [m.upper() for m in path_item if m.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE")]
                admin_routes.append((path, methods))

        assert len(admin_routes) > 0, "No admin routes found in the application"

        for path, methods in admin_routes:
            # Substitute path parameters with dummy UUIDs
            test_path = path.replace("{user_id}", "00000000-0000-0000-0000-000000000001")
            for method in methods:
                resp = test_client.request(method, test_path)
                assert resp.status_code == 401, (
                    f"{method} {test_path} returned {resp.status_code} without auth (expected 401)"
                )

    def test_no_admin_route_allows_regular_user(self, app: FastAPI):
        """Walk the app's routes and confirm all /api/admin/* paths return 403
        when called by a regular (non-admin) user."""
        test_client = TestClient(app)

        with (
            patch("core.supabase.anon_client") as mock_anon,
            patch("auth.dependencies.supabase_admin") as mock_admin,
        ):
            mock_client = MagicMock()
            mock_client.auth.get_user.return_value = SimpleNamespace(user=_make_fake_user())
            mock_anon.return_value = mock_client
            mock_admin.table.return_value.select.return_value.eq.return_value.execute.return_value = (
                _supabase_profile_response("user")
            )

            openapi_paths = app.openapi()["paths"]
            admin_routes: list[tuple[str, list[str]]] = []
            for path, path_item in openapi_paths.items():
                if "/admin" in path:
                    methods = [m.upper() for m in path_item if m.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE")]
                    admin_routes.append((path, methods))

            assert len(admin_routes) > 0, "No admin routes found in the application"

            for path, methods in admin_routes:
                test_path = path.replace("{user_id}", "00000000-0000-0000-0000-000000000001")
                for method in methods:
                    resp = test_client.request(
                        method,
                        test_path,
                        headers={"Authorization": "Bearer valid-user-token"},
                    )
                    assert resp.status_code == 403, (
                        f"{method} {test_path} returned {resp.status_code} for regular user (expected 403)"
                    )


# ---------------------------------------------------------------------------
# Edge case: profile not found → 403
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases for the require_admin_role dependency."""

    def test_no_profile_found_returns_403(self, client: TestClient):
        """If the user has no profile row, access is denied (403)."""
        with (
            patch("core.supabase.anon_client") as mock_anon,
            patch("auth.dependencies.supabase_admin") as mock_admin,
        ):
            mock_client = MagicMock()
            mock_client.auth.get_user.return_value = SimpleNamespace(user=_make_fake_user())
            mock_anon.return_value = mock_client

            mock_admin.table.return_value.select.return_value.eq.return_value.execute.return_value = (
                _supabase_empty_profile_response()
            )

            resp = client.get(
                "/api/admin/dashboard",
                headers={"Authorization": "Bearer valid-token"},
            )
            assert resp.status_code == 403

    def test_profile_lookup_error_returns_403(self, client: TestClient):
        """If the profile lookup fails, access is denied (403)."""
        with (
            patch("core.supabase.anon_client") as mock_anon,
            patch("auth.dependencies.supabase_admin") as mock_admin,
        ):
            mock_client = MagicMock()
            mock_client.auth.get_user.return_value = SimpleNamespace(user=_make_fake_user())
            mock_anon.return_value = mock_client

            mock_admin.table.return_value.select.return_value.eq.return_value.execute.side_effect = Exception(
                "DB connection lost"
            )

            resp = client.get(
                "/api/admin/dashboard",
                headers={"Authorization": "Bearer valid-token"},
            )
            assert resp.status_code == 403
