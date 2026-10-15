
from pydantic import BaseModel


class UserCredentials(BaseModel):
    email: str
    password: str


class SignupRequest(UserCredentials):
    name: str | None = None


class ForgotPasswordRequest(BaseModel):
    email: str
    redirect_to: str | None = None


class ResetPasswordRequest(BaseModel):
    new_password: str


class RefreshTokenRequest(BaseModel):
    refresh_token: str | None = None


class GoogleCallbackRequest(BaseModel):
    code: str
