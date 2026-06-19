"""Default JWT-backed :class:`AuthProvider`.

Wraps the existing token-decode + ``User.token_version`` invariant 1:1.
External apps replace this with their own ``AuthProvider`` (e.g. a Google
OAuth provider that validates ID tokens via Google's JWKS).

This provider performs:

1. Bearer header extraction.
2. JWT signature verification against ``config.secret_key``.
3. ``User.token_version`` equality check (the per-user revocation
   mechanism from §S4 of the v1 roadmap).

It does NOT check ``User.is_active`` — that lives in the UserProvider
because activeness is a user-level invariant, not a JWT invariant.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status
from fastapi.security import HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion.auth.password import dummy_verify_password, verify_password
from asterion.auth.revocation import is_token_revoked
from asterion.auth.tokens import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    get_subject_user_id,
    get_token_jti,
    get_token_version,
)
from asterion.models.user import User
from asterion.providers.base import (
    AuthIdentity,
    AuthSession,
    LoginCredentials,
    LoginError,
)

_bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(detail: str = "Invalid access token.") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


class BuiltinJWTAuthProvider:
    """Wraps the framework's existing JWT/Bearer flow.

    Implements :class:`asterion.providers.base.AuthProvider`.
    """

    async def authenticate_request(self, request: Request) -> AuthIdentity | None:
        credentials = await _bearer_scheme(request)
        if credentials is None or credentials.scheme.lower() != "bearer":
            return None

        config = request.app.state.asterion.config

        try:
            payload = decode_access_token(
                credentials.credentials,
                secret_key=config.secret_key,
                algorithm=config.jwt_algorithm,
                allow_impersonation=True,
                issuer=config.jwt_issuer,
                audience=config.jwt_audience,
            )
            user_id = get_subject_user_id(payload)
            token_version = get_token_version(payload)
            jti = get_token_jti(payload)
        except TokenError as exc:
            raise _unauthorized("Invalid access token.") from exc

        # token_version is a JWT-specific invariant; it does not belong on
        # the neutral AdminPrincipal DTO. We check it here so that external
        # auth providers (which don't have token_version) don't have to
        # know about this concept. The per-token jti revocation check
        # (Roadmap 3.2) rides on the same DB session.
        runtime = request.app.state.asterion
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            if await is_token_revoked(session, jti):
                raise _unauthorized("Token has been revoked.")
            stored_version = (
                await session.execute(select(User.token_version).where(User.id == user_id))
            ).scalar_one_or_none()

        if stored_version is None:
            raise _unauthorized("Invalid access token.")
        if stored_version != token_version:
            raise _unauthorized("Token has been revoked.")

        return AuthIdentity(user_id=str(user_id), claims=dict(payload))

    async def login(
        self,
        credentials: LoginCredentials,
        *,
        request: Request | None = None,
    ) -> AuthSession:
        """Verify email/password against the builtin ``User`` table and
        mint a framework JWT (Roadmap 2.6).

        Implements :class:`asterion.providers.base.CredentialAuthProvider`.

        Raises :class:`LoginError` with ``reason="invalid_credentials"``
        when the email is unknown or the password is wrong, and
        ``reason="inactive_user"`` when the account is disabled. The
        route layer maps these to HTTP status + audit reason and owns
        rate-limiting — this method is transport-agnostic.
        """
        if request is None:
            raise RuntimeError(
                "BuiltinJWTAuthProvider.login needs the request to reach the DB; "
                "external use should pass a CredentialAuthProvider that does not "
                "require it."
            )
        runtime = request.app.state.asterion
        config = runtime.config
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            user = (
                await session.execute(select(User).where(User.email == credentials.email))
            ).scalar_one_or_none()

        # Constant-time path (Review R15): on an unknown email, still spend one
        # bcrypt verify (against a dummy hash) so the response time matches a
        # wrong-password attempt and can't be used to enumerate accounts. Both
        # branches raise the SAME error/status.
        if user is None:
            dummy_verify_password(credentials.password)
            raise LoginError("invalid_credentials", "Invalid credentials.")
        if not verify_password(credentials.password, user.hashed_password):
            raise LoginError("invalid_credentials", "Invalid credentials.")
        if not user.is_active:
            raise LoginError("inactive_user", "User is inactive.")

        token = create_access_token(
            user.id,
            secret_key=config.secret_key,
            algorithm=config.jwt_algorithm,
            expires_minutes=config.access_token_expire_minutes,
            token_version=user.token_version,
            issuer=config.jwt_issuer,
            audience=config.jwt_audience,
        )
        refresh = create_refresh_token(
            user.id,
            secret_key=config.secret_key,
            algorithm=config.jwt_algorithm,
            expires_minutes=config.refresh_token_expire_minutes,
            token_version=user.token_version,
            issuer=config.jwt_issuer,
            audience=config.jwt_audience,
        )
        return AuthSession(
            access_token=token,
            token_type="bearer",
            expires_in=config.access_token_expire_minutes * 60,
            subject=str(user.id),
            refresh_token=refresh,
        )
