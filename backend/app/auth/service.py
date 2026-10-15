from typing import Any

from core.config import settings
from core.supabase import admin, anon_client
from fastapi import HTTPException


class AuthService:
    @staticmethod
    def signup(email: str, password: str, name: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"email": email, "password": password}
        if name:
            payload["options"] = {"data": {"name": name, "full_name": name}}
        try:
            res = anon_client().auth.sign_up(payload)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
        if res.user is None:
            raise HTTPException(status_code=400, detail="Signup failed")
        return {
            "message": "User registered successfully",
            "user": {"id": res.user.id, "email": res.user.email},
            "email_confirmation_required": res.session is None,
        }

    @staticmethod
    def login(email: str, password: str) -> dict[str, Any]:
        try:
            res = anon_client().auth.sign_in_with_password({"email": email, "password": password})
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid Email or Password")
        if not res.session or not res.user:
            raise HTTPException(status_code=401, detail="Invalid Email or Password")
        return {
            "access_token": res.session.access_token,
            "refresh_token": res.session.refresh_token,
            "expires_in": res.session.expires_in,
            "token_type": "bearer",
            "user": {"id": res.user.id, "email": res.user.email},
        }

    @staticmethod
    def get_google_auth_url(redirect_to: str | None = None) -> str:
        target_redirect = redirect_to or f"{settings.FRONTEND_URL}/auth/callback"
        try:
            res = anon_client().auth.sign_in_with_oauth(
                {"provider": "google", "options": {"redirect_to": target_redirect}}
            )
            return res.url
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @staticmethod
    def exchange_code_for_session(code: str) -> dict[str, Any]:
        """Exchange OAuth auth code (returned in callback) for user session tokens."""
        try:
            res = anon_client().auth.exchange_code_for_session({"auth_code": code})
            if not res or not res.session:
                raise HTTPException(status_code=400, detail="Failed to exchange authorization code")
            return {
                "access_token": res.session.access_token,
                "refresh_token": res.session.refresh_token,
                "expires_in": res.session.expires_in,
                "token_type": "bearer",
                "user": {"id": res.user.id, "email": res.user.email},
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=400, detail=f"Google authentication exchange failed: {str(e)}"
            )

    @staticmethod
    def forgot_password(email: str, redirect_to: str | None = None) -> dict[str, Any]:
        """Send password reset email to user."""
        target_redirect = redirect_to or f"{settings.FRONTEND_URL}/reset-password"
        try:
            anon_client().auth.reset_password_for_email(
                email, options={"redirect_to": target_redirect}
            )
            return {"message": "Password reset instructions sent to your email."}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @staticmethod
    def reset_password(token: str, new_password: str) -> dict[str, Any]:
        """Update password for the authenticated user using token/session."""
        try:
            client = anon_client()
            # Set the current user session using the token to allow updating password
            res = client.auth.get_user(token)
            if not res or not res.user:
                raise HTTPException(status_code=401, detail="Invalid or expired reset token")

            # Use admin client to safely update the user password by ID
            admin.auth.admin.update_user_by_id(res.user.id, {"password": new_password})
            return {"message": "Password has been successfully reset. You can now log in."}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Password reset failed: {str(e)}")

    @staticmethod
    def refresh_session(refresh_token: str) -> dict[str, Any]:
        """Obtain a new access token using a valid refresh token."""
        try:
            res = anon_client().auth.refresh_session(refresh_token)
            if not res or not res.session:
                raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
            return {
                "access_token": res.session.access_token,
                "refresh_token": res.session.refresh_token,
                "expires_in": res.session.expires_in,
                "token_type": "bearer",
                "user": {"id": res.user.id, "email": res.user.email},
            }
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=401, detail="Could not refresh session")
