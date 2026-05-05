from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from coreAdmin_api.auth import (
    create_access_token,
    create_access_token_with_iat,
    create_refresh_token,
    decode_token,
    verify_password,
)
from coreAdmin_api.database import get_db
from coreAdmin_api.dependencies import get_current_user
from coreAdmin_api.models.user import User
from coreAdmin_api.schemas.auth import LoginRequest, RefreshRequest, TokenResponse
from coreAdmin_api.schemas.session import SessionRead, SessionRevoke
from coreAdmin_api.schemas.user import UserPublic
from coreAdmin_api.services.session_security import session_security
from coreAdmin_api.settings import settings
from coreAdmin_api.token_blacklist import blacklist_token

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(request: Request, body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account inactive")

    # Use iat-embedded token so step-up checks can verify recency
    access_token = create_access_token_with_iat(str(user.id))
    refresh_token = create_refresh_token(str(user.id))

    # Register session for listing/revocation
    from jose import jwt as _jwt
    payload = _jwt.decode(access_token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    session_security.register(payload["jti"], user.id, exp, ip_address=ip, user_agent=ua)

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    payload = decode_token(body.refresh_token, expected_type="refresh")
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    # Impersonation tokens carry renewable=False; explicitly block any refresh token marked non-renewable
    if payload.get("renewable") is False:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token is not renewable"
        )

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    return TokenResponse(
        access_token=create_access_token(str(user.id)),
        refresh_token=create_refresh_token(str(user.id)),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    _: User = Depends(get_current_user),
):
    """Revoke the current access token by blacklisting its JTI."""
    payload = getattr(request.state, "token_payload", {})
    jti = payload.get("jti", "")
    exp = payload.get("exp", 0)
    if jti:
        blacklist_token(jti, exp)


@router.get("/me", response_model=UserPublic)
async def me(current_user: User = Depends(get_current_user)):
    return current_user


# ---------------------------------------------------------------------------
# Phase 12 — session management
# ---------------------------------------------------------------------------

@router.get("/sessions", response_model=list[SessionRead])
async def list_sessions(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """List active sessions for the current user."""
    records = session_security.list_for_user(current_user.id)
    return [
        SessionRead(
            jti=r.jti,
            user_id=r.user_id,
            created_at=r.created_at,
            expires_at=r.expires_at,
            ip_address=r.ip_address,
            user_agent=r.user_agent,
            is_active=r.is_active,
        )
        for r in records
    ]


@router.delete("/sessions/{jti}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    jti: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Revoke a specific session by JTI."""
    record = next(
        (r for r in session_security._sessions.values() if r.jti == jti), None
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    # Users can only revoke their own sessions; superadmins can revoke any
    if str(record.user_id) != str(current_user.id) and not current_user.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    session_security.revoke(jti)
    # Blacklist using the session's actual expiry so the JTI stays blocked until it naturally expires
    blacklist_token(jti, record.expires_at.timestamp())


@router.post("/step-up", status_code=status.HTTP_200_OK)
async def step_up(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """
    Check whether the current token was issued recently enough for step-up
    protected actions.  Returns 200 if the token is within STEP_UP_WINDOW_MINUTES,
    403 otherwise.  Clients must re-login to satisfy step-up requirements.
    """
    payload = getattr(request.state, "token_payload", {})
    iat = payload.get("iat")
    if iat is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token does not carry issue time; please re-login",
        )
    issued_at = datetime.fromtimestamp(iat, tz=timezone.utc)
    age = datetime.now(timezone.utc) - issued_at
    if age > timedelta(minutes=settings.STEP_UP_WINDOW_MINUTES):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Re-authentication required (token age exceeds {settings.STEP_UP_WINDOW_MINUTES} minutes)",
        )
    return {"step_up": True, "token_age_seconds": int(age.total_seconds())}
