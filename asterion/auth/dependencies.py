from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asterion.auth.revocation import is_token_revoked
from asterion.auth.tokens import (
    TokenError,
    decode_access_token,
    get_subject_user_id,
    get_token_jti,
    get_token_version,
    is_impersonation_token,
)
from asterion.db.dependencies import get_async_session
from asterion.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(detail: str = "Invalid access token.") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_async_session),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("Authentication required.")

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

    # Single-token revocation (Roadmap 3.2): a token whose jti was
    # logged out is rejected even though its signature + tkv are valid.
    if await is_token_revoked(session, jti):
        raise _unauthorized("Token has been revoked.")

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise _unauthorized("Invalid access token.")

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is inactive.",
        )

    if user.token_version != token_version:
        raise _unauthorized("Token has been revoked.")

    request.state.current_user = user
    request.state.token_payload = payload
    request.state.is_impersonating = is_impersonation_token(payload)

    return user


async def require_superadmin(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> User:
    payload = getattr(request.state, "token_payload", {})

    if is_impersonation_token(payload):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Impersonation tokens cannot access superadmin routes.",
        )

    if not current_user.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superadmin privileges required.",
        )

    return current_user
