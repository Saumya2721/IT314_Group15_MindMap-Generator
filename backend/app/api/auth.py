"""Authentication routes. Business logic and schemas are isolated in auth/."""


from auth.schemas import (
    ForgotPasswordRequest,
    GoogleCallbackRequest,
    RefreshTokenRequest,
    ResetPasswordRequest,
    SignupRequest,
    UserCredentials,
)
from auth.service import AuthService
from core.security import clear_auth_cookies, set_auth_cookies
from core.supabase import get_current_user
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/signup", status_code=200)
def signup(user: SignupRequest, response: Response):
    result = AuthService.signup(email=user.email, password=user.password, name=user.name)
    return result


@router.post("/login", status_code=200)
def login(user: UserCredentials, response: Response):
    session_data = AuthService.login(email=user.email, password=user.password)
    # Set standard HttpOnly cookies for secure session management
    set_auth_cookies(
        response=response,
        access_token=session_data["access_token"],
        refresh_token=session_data.get("refresh_token"),
        expires_in=session_data.get("expires_in", 3600),
    )
    return session_data


@router.get("/google", status_code=200)
def google_login(
    redirect_to: str | None = Query(
        None, description="Custom redirect URL after Google sign-in"
    ),
):
    auth_url = AuthService.get_google_auth_url(redirect_to=redirect_to)
    return {"google_auth_url": auth_url}


@router.post("/google/callback", status_code=200)
def google_callback(body: GoogleCallbackRequest, response: Response):
    session_data = AuthService.exchange_code_for_session(code=body.code)
    # Set standard HttpOnly cookies upon successful Google OAuth callback
    set_auth_cookies(
        response=response,
        access_token=session_data["access_token"],
        refresh_token=session_data.get("refresh_token"),
        expires_in=session_data.get("expires_in", 3600),
    )
    return session_data


@router.post("/forgot-password", status_code=200)
def forgot_password(body: ForgotPasswordRequest):
    return AuthService.forgot_password(email=body.email, redirect_to=body.redirect_to)


@router.post("/reset-password", status_code=200)
def reset_password(
    body: ResetPasswordRequest,
    request: Request,
    current_user=Depends(get_current_user),
):
    token = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
    elif request.cookies.get("access_token"):
        token = request.cookies.get("access_token")

    if not token:
        raise HTTPException(status_code=401, detail="Authentication required to reset password")

    return AuthService.reset_password(token=token, new_password=body.new_password)


@router.post("/refresh", status_code=200)
def refresh_token(request: Request, response: Response, body: RefreshTokenRequest | None = None):
    token = None
    if body and body.refresh_token:
        token = body.refresh_token
    elif request.cookies.get("refresh_token"):
        token = request.cookies.get("refresh_token")

    if not token:
        raise HTTPException(status_code=401, detail="Refresh token required")

    session_data = AuthService.refresh_session(token)
    set_auth_cookies(
        response=response,
        access_token=session_data["access_token"],
        refresh_token=session_data.get("refresh_token"),
        expires_in=session_data.get("expires_in", 3600),
    )
    return session_data


@router.post("/logout", status_code=200)
def logout(response: Response):
    clear_auth_cookies(response)
    return {"message": "Logged out successfully"}
