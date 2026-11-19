"""Unit tests for auth routes guarding OAuth cookies and redirect security."""

from unittest.mock import patch

from core.config import settings
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_google_login_cookie_has_secure_flag():
    with patch("auth.service.AuthService.get_google_auth_url") as mock_auth_url:
        mock_auth_url.return_value = {
            "url": "https://accounts.google.com/o/oauth2/auth",
            "code_verifier": "mock-code-verifier",
        }
        res = client.get("/auth/google")
        assert res.status_code == 200
        # Cookie verification
        set_cookie_header = res.headers.get("set-cookie", "")
        assert "oauth_code_verifier=mock-code-verifier" in set_cookie_header
        assert "HttpOnly" in set_cookie_header
        assert "SameSite=lax" in set_cookie_header or "samesite=lax" in set_cookie_header.lower()
        if settings.COOKIE_SECURE:
            assert "secure" in set_cookie_header.lower()


def test_google_callback_get_redirects_safely_to_frontend():
    res = client.get(
        "/auth/callback",
        params={"code": "valid_code_abc", "error": None},
        follow_redirects=False,
    )
    assert res.status_code == 307
    location = res.headers["location"]
    assert location.startswith(f"{settings.FRONTEND_URL}/auth/callback?")
    assert "code=valid_code_abc" in location


def test_google_callback_get_handles_error_params_safely():
    res = client.get(
        "/auth/callback",
        params={"error": "access_denied", "error_description": "User cancelled"},
        follow_redirects=False,
    )
    assert res.status_code == 307
    location = res.headers["location"]
    assert location.startswith(f"{settings.FRONTEND_URL}/auth/callback?")
    assert "error=access_denied" in location
    assert "error_description=User+cancelled" in location or "error_description=User%20cancelled" in location


def test_google_callback_get_no_params():
    res = client.get("/auth/callback", follow_redirects=False)
    assert res.status_code == 307
    assert res.headers["location"] == f"{settings.FRONTEND_URL}/auth/callback"
