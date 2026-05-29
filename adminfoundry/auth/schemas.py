from __future__ import annotations

from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    #: ``None`` when the response is a 2FA challenge (Roadmap 3.4b):
    #: the caller must POST ``mfa_token`` + a code to ``/auth/2fa/login``
    #: to get a real access token. Clients branch on ``mfa_required``
    #: to decide which it is.
    access_token: str | None = None
    token_type: str = "bearer"
    #: Present when the auth provider issues refresh tokens (Roadmap
    #: 3.1). Clients store it and POST it to ``/auth/refresh`` to get a
    #: new access+refresh pair without re-entering credentials.
    refresh_token: str | None = None
    #: True when the user has 2FA enabled and the login is incomplete
    #: until a code is verified at ``/auth/2fa/login`` (3.4b). Default
    #: False for the cache-friendly non-MFA path.
    mfa_required: bool = False
    #: Short-lived challenge token exchanged at ``/auth/2fa/login`` for
    #: the real access+refresh pair. Set iff ``mfa_required``.
    mfa_token: str | None = None


class RefreshRequest(BaseModel):
    refresh_token: str


class PasswordResetRequestBody(BaseModel):
    email: EmailStr


class PasswordResetConfirmBody(BaseModel):
    token: str
    new_password: str


class TwoFactorSetupResponse(BaseModel):
    secret: str
    provisioning_uri: str


class TwoFactorEnableBody(BaseModel):
    code: str


class TwoFactorEnableResponse(BaseModel):
    backup_codes: list[str]


class TwoFactorDisableBody(BaseModel):
    code: str


class TwoFactorLoginBody(BaseModel):
    mfa_token: str
    #: Either ``code`` (live TOTP from authenticator) or ``backup_code``
    #: (single-use). Exactly one must be present.
    code: str | None = None
    backup_code: str | None = None


class MeResponse(BaseModel):
    id: str
    email: EmailStr
    full_name: str | None = None
    is_active: bool
    is_superadmin: bool
    is_impersonating: bool = False
