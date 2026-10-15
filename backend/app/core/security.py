
from core.config import settings
from fastapi import Response


def set_auth_cookies(
    response: Response,
    access_token: str,
    refresh_token: str | None = None,
    expires_in: int = 3600,
) -> None:
    """Set secure, HttpOnly cookies for access and refresh tokens."""
    response.set_cookie(
        key=settings.COOKIE_ACCESS_TOKEN_KEY,
        value=access_token,
        max_age=expires_in,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        domain=settings.COOKIE_DOMAIN,
        path="/",
    )
    if refresh_token:
        # Refresh tokens typically have longer lifetimes (e.g. 30 days)
        refresh_max_age = 30 * 24 * 3600
        response.set_cookie(
            key=settings.COOKIE_REFRESH_TOKEN_KEY,
            value=refresh_token,
            max_age=refresh_max_age,
            httponly=True,
            secure=settings.COOKIE_SECURE,
            samesite=settings.COOKIE_SAMESITE,
            domain=settings.COOKIE_DOMAIN,
            path="/",
        )


def clear_auth_cookies(response: Response) -> None:
    """Clear access and refresh token cookies upon logout."""
    response.delete_cookie(
        key=settings.COOKIE_ACCESS_TOKEN_KEY,
        domain=settings.COOKIE_DOMAIN,
        path="/",
    )
    response.delete_cookie(
        key=settings.COOKIE_REFRESH_TOKEN_KEY,
        domain=settings.COOKIE_DOMAIN,
        path="/",
    )
