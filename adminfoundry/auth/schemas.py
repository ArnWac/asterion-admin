from __future__ import annotations

from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    #: Present when the auth provider issues refresh tokens (Roadmap
    #: 3.1). Clients store it and POST it to ``/auth/refresh`` to get a
    #: new access+refresh pair without re-entering credentials.
    refresh_token: str | None = None


class RefreshRequest(BaseModel):
    refresh_token: str


class PasswordResetRequestBody(BaseModel):
    email: EmailStr


class PasswordResetConfirmBody(BaseModel):
    token: str
    new_password: str


class MeResponse(BaseModel):
    id: str
    email: EmailStr
    full_name: str | None = None
    is_active: bool
    is_superadmin: bool
    is_impersonating: bool = False
