from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from adminfoundry.database import get_db
from adminfoundry.models.user import User
from adminfoundry.auth import decode_token
from adminfoundry.token_blacklist import is_blacklisted

bearer_scheme = HTTPBearer()


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    payload = decode_token(credentials.credentials, expected_type="access")
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    jti = payload.get("jti", "")
    if is_blacklisted(jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked"
        )

    # Store payload for downstream use (logout, impersonation checks, audit middleware)
    request.state.token_payload = payload

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    # Set for audit middleware
    request.state.audit_user_id = str(user.id)

    return user


async def require_superadmin(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Superadmin required")
    # Impersonation tokens are rejected on superadmin-only routes
    payload = getattr(request.state, "token_payload", {})
    if payload.get("impersonated_by"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Impersonation tokens cannot access superadmin routes",
        )
    return current_user


def require_role(role_name: str):
    """Dependency factory — passes if user is superadmin or has the named role."""

    async def _check(current_user: User = Depends(get_current_user)) -> User:
        if current_user.is_superadmin:
            return current_user
        if not any(r.name == role_name for r in current_user.roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{role_name}' required",
            )
        return current_user

    return _check
