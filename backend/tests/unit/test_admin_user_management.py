"""Unit tests for Admin User Management and Audit Logging APIs.

Covers:
1. GET /api/admin/users: pagination, metadata, content protection, 401/403.
2. POST /api/admin/users/{user_id}/suspend: success, audit log, 404, self-suspend 400, 401/403.
3. POST /api/admin/users/{user_id}/unsuspend: success, audit log, 404, 401/403.
4. DELETE /api/admin/users/{user_id}: storage cleanup, DB delete, Supabase auth delete, audit log, 404, self-delete 400.
5. Storage cleanup function (delete_user_storage_files).
6. Security: Suspended users receive 403 Forbidden on login and token refresh attempts.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from auth.service import AuthService
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from uploads.service import delete_user_storage_files


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


def _patch_admin_auth(admin_id: str = "00000000-0000-0000-0000-000000000099"):
    """Context manager to authenticate as admin."""

    class _Ctx:
        def __enter__(self_ctx):
            self_ctx.p1 = patch("core.supabase.anon_client")
            self_ctx.p2 = patch("auth.dependencies.supabase_admin")
            mock_anon = self_ctx.p1.__enter__()
            mock_admin_client = self_ctx.p2.__enter__()

            mock_client = MagicMock()
            mock_client.auth.get_user.return_value = SimpleNamespace(
                user=_make_fake_user(user_id=admin_id, email="admin@example.com")
            )
            mock_anon.return_value = mock_client

            mock_admin_client.table.return_value.select.return_value.eq.return_value.execute.return_value = (
                _supabase_profile_response("admin")
            )
            return self_ctx

        def __exit__(self_ctx, *args):
            self_ctx.p1.__exit__(*args)
            self_ctx.p2.__exit__(*args)

    return _Ctx()


def _patch_regular_user_auth(user_id: str = "00000000-0000-0000-0000-000000000002"):
    """Context manager to authenticate as regular user."""

    class _Ctx:
        def __enter__(self_ctx):
            self_ctx.p1 = patch("core.supabase.anon_client")
            self_ctx.p2 = patch("auth.dependencies.supabase_admin")
            mock_anon = self_ctx.p1.__enter__()
            mock_admin_client = self_ctx.p2.__enter__()

            mock_client = MagicMock()
            mock_client.auth.get_user.return_value = SimpleNamespace(
                user=_make_fake_user(user_id=user_id, email="user@example.com")
            )
            mock_anon.return_value = mock_client

            mock_admin_client.table.return_value.select.return_value.eq.return_value.execute.return_value = (
                _supabase_profile_response("user")
            )
            return self_ctx

        def __exit__(self_ctx, *args):
            self_ctx.p1.__exit__(*args)
            self_ctx.p2.__exit__(*args)

    return _Ctx()


# ---------------------------------------------------------------------------
# 1. GET /api/admin/users
# ---------------------------------------------------------------------------


class TestListUsers:
    """Test user listing with pagination and strict metadata isolation."""

    def test_list_users_pagination_and_metadata(self, client: TestClient):
        target_id = uuid4()
        now = datetime.now(UTC)
        fake_rows = [
            {
                "id": target_id,
                "email": "user1@example.com",
                "created_at": now,
                "status": "active",
                "map_count": 3,
            }
        ]

        with _patch_admin_auth():
            mock_pool = MagicMock()
            mock_pool.fetch = AsyncMock(return_value=fake_rows)
            cast(FastAPI, client.app).state.pool = mock_pool

            resp = client.get(
                "/api/admin/users?limit=10&offset=5",
                headers={"Authorization": "Bearer admin-token"},
            )

            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, list)
            assert len(data) == 1

            user_item = data[0]
            assert user_item["id"] == str(target_id)
            assert user_item["email"] == "user1@example.com"
            assert user_item["status"] == "active"
            assert user_item["map_count"] == 3
            assert "created_at" in user_item

            # Verify pagination args were forwarded to DB query
            assert mock_pool.fetch.called
            call_args = mock_pool.fetch.call_args[0]
            assert call_args[1] == 10  # limit
            assert call_args[2] == 5  # offset

    def test_list_users_never_exposes_user_content_fields(self, client: TestClient):
        """Verify that user content fields (prompts, nodes, chat, files) are omitted."""
        target_id = uuid4()
        fake_rows = [
            {
                "id": target_id,
                "email": "user2@example.com",
                "created_at": datetime.now(UTC),
                "status": "suspended",
                "map_count": 0,
            }
        ]

        with _patch_admin_auth():
            mock_pool = MagicMock()
            mock_pool.fetch = AsyncMock(return_value=fake_rows)
            cast(FastAPI, client.app).state.pool = mock_pool

            resp = client.get(
                "/api/admin/users",
                headers={"Authorization": "Bearer admin-token"},
            )

            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            user_item = data[0]

            forbidden_content_keys = {
                "nodes",
                "edges",
                "title",
                "content",
                "prompt",
                "messages",
                "summary",
                "raw_text",
            }
            present_keys = set(user_item.keys())
            assert forbidden_content_keys.isdisjoint(present_keys)
            assert present_keys == {"id", "email", "created_at", "status", "map_count"}

    def test_list_users_forbidden_for_regular_user(self, client: TestClient):
        with _patch_regular_user_auth():
            resp = client.get(
                "/api/admin/users",
                headers={"Authorization": "Bearer regular-token"},
            )
            assert resp.status_code == 403

    def test_list_users_unauthorized_without_token(self, client: TestClient):
        resp = client.get("/api/admin/users")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 2. POST /api/admin/users/{user_id}/suspend
# ---------------------------------------------------------------------------


class TestSuspendUser:
    """Test suspending users and audit trail immutability."""

    def test_suspend_user_success(self, client: TestClient):
        admin_id = "00000000-0000-0000-0000-000000000099"
        target_id = uuid4()

        with _patch_admin_auth(admin_id=admin_id):
            mock_pool = MagicMock()
            mock_pool.fetchrow = AsyncMock(return_value={"id": target_id})
            mock_pool.execute = AsyncMock(return_value=None)
            cast(FastAPI, client.app).state.pool = mock_pool

            resp = client.post(
                f"/api/admin/users/{target_id}/suspend",
                headers={"Authorization": "Bearer admin-token"},
            )

            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "suspended"
            assert data["user_id"] == str(target_id)
            assert "message" in data

            # Verify profiles UPDATE was executed
            calls = mock_pool.execute.call_args_list
            update_call = calls[0]
            assert "UPDATE public.profiles SET suspended_at = now()" in update_call[0][0]
            assert update_call[0][1] == target_id

            # Verify immutable audit log was inserted
            audit_call = calls[1]
            assert "INSERT INTO public.admin_audit_log" in audit_call[0][0]
            assert audit_call[0][1] == UUID(admin_id)  # actor_id
            assert audit_call[0][2] == "user.suspend"  # action
            assert audit_call[0][3] == target_id  # target_user_id

    def test_suspend_user_not_found(self, client: TestClient):
        target_id = uuid4()

        with _patch_admin_auth():
            mock_pool = MagicMock()
            mock_pool.fetchrow = AsyncMock(return_value=None)
            cast(FastAPI, client.app).state.pool = mock_pool

            resp = client.post(
                f"/api/admin/users/{target_id}/suspend",
                headers={"Authorization": "Bearer admin-token"},
            )

            assert resp.status_code == 404
            assert "User not found" in resp.json()["detail"]

    def test_suspend_self_prevented(self, client: TestClient):
        admin_id = "00000000-0000-0000-0000-000000000099"

        with _patch_admin_auth(admin_id=admin_id):
            mock_pool = MagicMock()
            mock_pool.fetchrow = AsyncMock(return_value={"id": UUID(admin_id)})
            cast(FastAPI, client.app).state.pool = mock_pool

            resp = client.post(
                f"/api/admin/users/{admin_id}/suspend",
                headers={"Authorization": "Bearer admin-token"},
            )

            assert resp.status_code == 400
            assert "Cannot suspend your own" in resp.json()["detail"]

    def test_suspend_audit_failure_raises_500(self, client: TestClient):
        target_id = uuid4()

        with _patch_admin_auth():
            mock_pool = MagicMock()
            mock_pool.fetchrow = AsyncMock(return_value={"id": target_id})
            # First call (UPDATE) succeeds, second call (audit log INSERT) fails
            mock_pool.execute = AsyncMock(side_effect=[None, RuntimeError("DB write error")])
            cast(FastAPI, client.app).state.pool = mock_pool

            resp = client.post(
                f"/api/admin/users/{target_id}/suspend",
                headers={"Authorization": "Bearer admin-token"},
            )

            assert resp.status_code == 500
            assert "Failed to record audit log" in resp.json()["detail"]

    def test_suspend_user_forbidden_for_regular_user(self, client: TestClient):
        target_id = uuid4()
        with _patch_regular_user_auth():
            resp = client.post(
                f"/api/admin/users/{target_id}/suspend",
                headers={"Authorization": "Bearer regular-token"},
            )
            assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 3. POST /api/admin/users/{user_id}/unsuspend
# ---------------------------------------------------------------------------


class TestUnsuspendUser:
    """Test restoring access to a suspended user."""

    def test_unsuspend_user_success(self, client: TestClient):
        admin_id = "00000000-0000-0000-0000-000000000099"
        target_id = uuid4()

        with _patch_admin_auth(admin_id=admin_id):
            mock_pool = MagicMock()
            mock_pool.fetchrow = AsyncMock(return_value={"id": target_id})
            mock_pool.execute = AsyncMock(return_value=None)
            cast(FastAPI, client.app).state.pool = mock_pool

            resp = client.post(
                f"/api/admin/users/{target_id}/unsuspend",
                headers={"Authorization": "Bearer admin-token"},
            )

            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "active"
            assert data["user_id"] == str(target_id)
            assert "message" in data

            # Verify profiles UPDATE was executed
            calls = mock_pool.execute.call_args_list
            update_call = calls[0]
            assert "UPDATE public.profiles SET suspended_at = NULL" in update_call[0][0]
            assert update_call[0][1] == target_id

            # Verify immutable audit log was inserted
            audit_call = calls[1]
            assert "INSERT INTO public.admin_audit_log" in audit_call[0][0]
            assert audit_call[0][1] == UUID(admin_id)  # actor_id
            assert audit_call[0][2] == "user.unsuspend"  # action
            assert audit_call[0][3] == target_id  # target_user_id

    def test_unsuspend_user_not_found(self, client: TestClient):
        target_id = uuid4()

        with _patch_admin_auth():
            mock_pool = MagicMock()
            mock_pool.fetchrow = AsyncMock(return_value=None)
            cast(FastAPI, client.app).state.pool = mock_pool

            resp = client.post(
                f"/api/admin/users/{target_id}/unsuspend",
                headers={"Authorization": "Bearer admin-token"},
            )

            assert resp.status_code == 404
            assert "User not found" in resp.json()["detail"]

    def test_unsuspend_user_forbidden_for_regular_user(self, client: TestClient):
        target_id = uuid4()
        with _patch_regular_user_auth():
            resp = client.post(
                f"/api/admin/users/{target_id}/unsuspend",
                headers={"Authorization": "Bearer regular-token"},
            )
            assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 4. DELETE /api/admin/users/{user_id}
# ---------------------------------------------------------------------------


class TestDeleteUser:
    """Test user deletion cascade, storage cleanup, and Supabase auth deletion."""

    def test_delete_user_success(self, client: TestClient):
        admin_id = "00000000-0000-0000-0000-000000000099"
        target_id = uuid4()

        with (
            _patch_admin_auth(admin_id=admin_id),
            patch("routes.admin.delete_user_storage_files", AsyncMock(return_value=["file1.pdf"])) as mock_storage,
            patch("routes.admin.admin") as mock_supa_admin,
        ):
            mock_pool = MagicMock()
            mock_pool.fetchrow = AsyncMock(return_value={"id": target_id})
            mock_pool.execute = AsyncMock(return_value=None)
            cast(FastAPI, client.app).state.pool = mock_pool

            mock_supa_admin.auth.admin.delete_user = MagicMock()

            resp = client.delete(
                f"/api/admin/users/{target_id}",
                headers={"Authorization": "Bearer admin-token"},
            )

            assert resp.status_code == 200
            data = resp.json()
            assert data["user_id"] == str(target_id)
            assert "deleted successfully" in data["message"]

            # 1. Storage cleanup called
            mock_storage.assert_called_once_with(user_id=target_id, pool=mock_pool)

            # 2. Audit log recorded
            calls = mock_pool.execute.call_args_list
            audit_call = calls[0]
            assert "INSERT INTO public.admin_audit_log" in audit_call[0][0]
            assert audit_call[0][1] == UUID(admin_id)
            assert audit_call[0][2] == "user.delete"

            # 3. DB cascade delete executed
            delete_call = calls[1]
            assert "DELETE FROM public.profiles WHERE id = $1" in delete_call[0][0]
            assert delete_call[0][1] == target_id

            # 4. Supabase auth user deleted
            mock_supa_admin.auth.admin.delete_user.assert_called_once_with(str(target_id))

    def test_delete_user_not_found(self, client: TestClient):
        target_id = uuid4()

        with _patch_admin_auth():
            mock_pool = MagicMock()
            mock_pool.fetchrow = AsyncMock(return_value=None)
            cast(FastAPI, client.app).state.pool = mock_pool

            resp = client.delete(
                f"/api/admin/users/{target_id}",
                headers={"Authorization": "Bearer admin-token"},
            )

            assert resp.status_code == 404
            assert "User not found" in resp.json()["detail"]

    def test_delete_self_prevented(self, client: TestClient):
        admin_id = "00000000-0000-0000-0000-000000000099"

        with _patch_admin_auth(admin_id=admin_id):
            mock_pool = MagicMock()
            mock_pool.fetchrow = AsyncMock(return_value={"id": UUID(admin_id)})
            cast(FastAPI, client.app).state.pool = mock_pool

            resp = client.delete(
                f"/api/admin/users/{admin_id}",
                headers={"Authorization": "Bearer admin-token"},
            )

            assert resp.status_code == 400
            assert "Cannot delete your own" in resp.json()["detail"]

    def test_delete_user_storage_error_handled_gracefully(self, client: TestClient):
        """Storage failure should log warning and not abort database/auth deletion."""
        admin_id = "00000000-0000-0000-0000-000000000099"
        target_id = uuid4()

        with (
            _patch_admin_auth(admin_id=admin_id),
            patch("routes.admin.delete_user_storage_files", AsyncMock(side_effect=RuntimeError("Storage unreachable"))),
            patch("routes.admin.admin") as mock_supa_admin,
        ):
            mock_pool = MagicMock()
            mock_pool.fetchrow = AsyncMock(return_value={"id": target_id})
            mock_pool.execute = AsyncMock(return_value=None)
            cast(FastAPI, client.app).state.pool = mock_pool

            resp = client.delete(
                f"/api/admin/users/{target_id}",
                headers={"Authorization": "Bearer admin-token"},
            )

            assert resp.status_code == 200
            # Database and Supabase auth deletions still proceeded
            assert mock_supa_admin.auth.admin.delete_user.called


# ---------------------------------------------------------------------------
# 5. Storage Cleanup Function (delete_user_storage_files)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_user_storage_files_success():
    target_id = uuid4()
    mock_pool = MagicMock()
    mock_pool.fetch = AsyncMock(return_value=[{"storage_key": f"{target_id}/map1/job1/0_file.pdf"}])

    with patch("uploads.service.admin") as mock_admin:
        # Mock folder listing
        mock_admin.storage.from_().list.side_effect = [
            [{"name": "map1"}],  # list(target_id)
            [{"name": "job1"}],  # list(target_id/map1)
            [{"name": "0_file.pdf"}],  # list(target_id/map1/job1)
        ]
        mock_admin.storage.from_().remove = MagicMock(return_value=[])

        deleted = await delete_user_storage_files(user_id=target_id, pool=mock_pool)
        assert len(deleted) >= 1
        mock_admin.storage.from_().remove.assert_called_once()


# ---------------------------------------------------------------------------
# 6. Security Enforcement: Suspended User Access
# ---------------------------------------------------------------------------


class TestSuspendedUserSecurity:
    """Ensure suspended users cannot authenticate or refresh tokens."""

    def test_login_active_user_succeeds(self):
        user_id = str(uuid4())
        mock_session = SimpleNamespace(
            access_token="valid-token",
            refresh_token="valid-refresh",
            expires_in=3600,
        )
        mock_user = SimpleNamespace(id=user_id, email="active@example.com")

        with (
            patch("auth.service.anon_client") as mock_anon,
            patch("auth.service.admin") as mock_admin,
        ):
            mock_client = MagicMock()
            mock_client.auth.sign_in_with_password.return_value = SimpleNamespace(
                session=mock_session,
                user=mock_user,
            )
            mock_anon.return_value = mock_client

            # Profile has suspended_at = None
            mock_admin.table.return_value.select.return_value.eq.return_value.execute.return_value = (
                _supabase_profile_response("user", suspended=False)
            )

            result = AuthService.login("active@example.com", "password123")
            assert result["access_token"] == "valid-token"
            assert result["user"]["email"] == "active@example.com"

    def test_login_suspended_user_forbidden(self):
        user_id = str(uuid4())
        mock_session = SimpleNamespace(
            access_token="valid-token",
            refresh_token="valid-refresh",
            expires_in=3600,
        )
        mock_user = SimpleNamespace(id=user_id, email="suspended@example.com")

        with (
            patch("auth.service.anon_client") as mock_anon,
            patch("auth.service.admin") as mock_admin,
        ):
            mock_client = MagicMock()
            mock_client.auth.sign_in_with_password.return_value = SimpleNamespace(
                session=mock_session,
                user=mock_user,
            )
            mock_anon.return_value = mock_client

            # Profile has suspended_at set
            mock_admin.table.return_value.select.return_value.eq.return_value.execute.return_value = (
                _supabase_profile_response("user", suspended=True)
            )

            with pytest.raises(HTTPException) as exc_info:
                AuthService.login("suspended@example.com", "password123")

            assert exc_info.value.status_code == 403
            assert "Account is suspended" in exc_info.value.detail

    def test_refresh_token_suspended_user_forbidden(self):
        user_id = str(uuid4())
        mock_session = SimpleNamespace(
            access_token="new-token",
            refresh_token="new-refresh",
            expires_in=3600,
        )
        mock_user = SimpleNamespace(id=user_id, email="suspended@example.com")

        with (
            patch("auth.service.anon_client") as mock_anon,
            patch("auth.service.admin") as mock_admin,
        ):
            mock_client = MagicMock()
            mock_client.auth.refresh_session.return_value = SimpleNamespace(
                session=mock_session,
                user=mock_user,
            )
            mock_anon.return_value = mock_client

            # Profile has suspended_at set
            mock_admin.table.return_value.select.return_value.eq.return_value.execute.return_value = (
                _supabase_profile_response("user", suspended=True)
            )

            with pytest.raises(HTTPException) as exc_info:
                AuthService.refresh_session("some-refresh-token")

            assert exc_info.value.status_code == 403
            assert "Account is suspended" in exc_info.value.detail

    def test_refresh_token_active_user_succeeds(self):
        user_id = str(uuid4())
        mock_session = SimpleNamespace(
            access_token="new-token",
            refresh_token="new-refresh",
            expires_in=3600,
        )
        mock_user = SimpleNamespace(id=user_id, email="active@example.com")

        with (
            patch("auth.service.anon_client") as mock_anon,
            patch("auth.service.admin") as mock_admin,
        ):
            mock_client = MagicMock()
            mock_client.auth.refresh_session.return_value = SimpleNamespace(
                session=mock_session,
                user=mock_user,
            )
            mock_anon.return_value = mock_client

            mock_admin.table.return_value.select.return_value.eq.return_value.execute.return_value = (
                _supabase_profile_response("user", suspended=False)
            )

            result = AuthService.refresh_session("some-refresh-token")
            assert result["access_token"] == "new-token"
