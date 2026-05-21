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

from adminfoundry.auth.tokens import (
    TokenError,
    decode_access_token,
    get_subject_user_id,
    get_token_version,
)
from adminfoundry.models.user import User
from adminfoundry.providers.base import AuthIdentity

_bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(detail: str = "Invalid access token.") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


class BuiltinJWTAuthProvider:
    """Wraps the framework's existing JWT/Bearer flow.

    Implements :class:`adminfoundry.providers.base.AuthProvider`.
    """

    async def authenticate_request(self, request: Request) -> AuthIdentity | None:
        credentials = await _bearer_scheme(request)
        if credentials is None or credentials.scheme.lower() != "bearer":
            return None

        config = request.app.state.adminfoundry.config

        try:
            payload = decode_access_token(
                credentials.credentials,
                secret_key=config.secret_key,
                algorithm=config.jwt_algorithm,
                allow_impersonation=True,
            )
            user_id = get_subject_user_id(payload)
            token_version = get_token_version(payload)
        except TokenError as exc:
            raise _unauthorized("Invalid access token.") from exc

        # token_version is a JWT-specific invariant; it does not belong on
        # the neutral AdminUser DTO. We check it here so that external
        # auth providers (which don't have token_version) don't have to
        # know about this concept.
        runtime = request.app.state.adminfoundry
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            stored_version = (
                await session.execute(
                    select(User.token_version).where(User.id == user_id)
                )
            ).scalar_one_or_none()

        if stored_version is None:
            raise _unauthorized("Invalid access token.")
        if stored_version != token_version:
            raise _unauthorized("Token has been revoked.")

        return AuthIdentity(user_id=str(user_id), claims=dict(payload))
