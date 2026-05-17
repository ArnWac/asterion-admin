import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from adminfoundry.auth import (
    create_access_token,
    create_access_token_with_iat,
    create_mfa_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from adminfoundry.database import get_db
from adminfoundry.dependencies import get_current_user
from adminfoundry.models.tenant_membership import TenantMembership
from adminfoundry.models.user import User


async def _gate_tenant_membership(user: User, request: Request, db: AsyncSession) -> None:
    """Raise 403 if user has no active TenantMembership for the request's tenant."""
    tenant = getattr(request.state, "tenant", None)
    if tenant is None or user.is_superadmin:
        return
    exists = (
        await db.execute(
            select(TenantMembership)
            .where(TenantMembership.user_id == user.id)
            .where(TenantMembership.tenant_id == tenant.id)
            .where(TenantMembership.is_active == True)  # noqa: E712
        )
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this tenant",
        )
from adminfoundry.schemas.auth import (
    LoginRequest,
    LoginResponse,
    MFAVerifyRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshRequest,
    TokenResponse,
    TwoFASetupResponse,
)
from adminfoundry.schemas.session import SessionRead, SessionRevoke
from adminfoundry.schemas.user import UserPublic
from adminfoundry.services.session_security import session_security
from adminfoundry.settings import settings
from adminfoundry.token_blacklist import blacklist_token, is_blacklisted
from adminfoundry.login_security import is_locked, record_failure, clear_failures
from adminfoundry import signals as _signals

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _totp_valid(secret: str, code: str) -> bool:
    try:
        import pyotp
        return pyotp.TOTP(secret).verify(code, valid_window=1)
    except Exception:
        return False


def _hash_backup(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _check_backup_code(user: User, code: str) -> bool:
    """Verify code against stored backup codes and consume it if valid."""
    if not user.totp_backup_codes:
        return False
    h = _hash_backup(code)
    remaining = [c for c in user.totp_backup_codes if c != h]
    if len(remaining) == len(user.totp_backup_codes):
        return False  # not found
    user.totp_backup_codes = remaining or None
    return True


@router.post("/login", response_model=LoginResponse)
async def login(request: Request, body: LoginRequest, db: AsyncSession = Depends(get_db)):
    email = body.email.lower()

    if is_locked(email):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Account temporarily locked due to repeated login failures",
        )

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.hashed_password):
        record_failure(email)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account inactive")

    await _gate_tenant_membership(user, request, db)
    clear_failures(email)

    if settings.ENFORCE_2FA_FOR_SUPERADMIN and user.is_superadmin and not user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superadmin accounts must enable two-factor authentication before logging in",
        )

    if user.totp_enabled:
        mfa_token = create_mfa_token(str(user.id))
        return LoginResponse(mfa_required=True, mfa_token=mfa_token)

    access_token = create_access_token_with_iat(str(user.id), token_version=user.token_version)
    refresh_token = create_refresh_token(str(user.id))

    from jose import jwt as _jwt
    payload = _jwt.decode(access_token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    session_security.register(payload["jti"], user.id, exp, ip_address=ip, user_agent=ua)

    await _signals.emit("post_login", user=user, request=request)
    return LoginResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/2fa/verify", response_model=LoginResponse)
async def verify_2fa(
    request: Request,
    body: MFAVerifyRequest,
    db: AsyncSession = Depends(get_db),
):
    """Complete MFA login — exchange mfa_token + TOTP code for full access tokens."""
    payload = decode_token(body.mfa_token, expected_type="mfa")
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired MFA token")

    user = (await db.execute(select(User).where(User.id == payload["sub"]))).scalar_one_or_none()
    if user is None or not user.is_active or not user.totp_enabled:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid MFA session")

    await _gate_tenant_membership(user, request, db)
    code_ok = _totp_valid(user.totp_secret, body.code) or _check_backup_code(user, body.code)
    if not code_ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication code")

    await db.commit()  # persist consumed backup code if any

    access_token = create_access_token_with_iat(str(user.id), token_version=user.token_version)
    refresh_token = create_refresh_token(str(user.id))

    from jose import jwt as _jwt
    token_payload = _jwt.decode(access_token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    exp = datetime.fromtimestamp(token_payload["exp"], tz=timezone.utc)
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    session_security.register(token_payload["jti"], user.id, exp, ip_address=ip, user_agent=ua)

    await _signals.emit("post_login", user=user, request=request)
    return LoginResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/2fa/setup", response_model=TwoFASetupResponse)
async def setup_2fa(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a new TOTP secret. Returns the otpauth:// URI and one-time backup codes.
    Call /2fa/enable with a valid code to activate 2FA."""
    try:
        import pyotp
    except ImportError:
        raise HTTPException(status_code=503, detail="pyotp not installed — add the [2fa] extra")

    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=current_user.email, issuer_name=settings.TOTP_ISSUER)

    # Generate 8 backup codes — shown once, stored hashed
    backup_codes = [secrets.token_hex(4) for _ in range(8)]
    current_user.totp_secret = secret
    current_user.totp_backup_codes = [_hash_backup(c) for c in backup_codes]
    await db.commit()

    return TwoFASetupResponse(totp_uri=uri, backup_codes=backup_codes)


@router.post("/2fa/enable", status_code=status.HTTP_200_OK)
async def enable_2fa(
    body: MFAVerifyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Confirm TOTP setup with a valid code and activate 2FA on the account."""
    if not current_user.totp_secret:
        raise HTTPException(status_code=400, detail="Run /2fa/setup first")
    if not _totp_valid(current_user.totp_secret, body.code):
        raise HTTPException(status_code=400, detail="Invalid code")
    current_user.totp_enabled = True
    await db.commit()
    return {"2fa": "enabled"}


@router.post("/2fa/disable", status_code=status.HTTP_200_OK)
async def disable_2fa(
    body: MFAVerifyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Disable 2FA — requires a valid TOTP code or backup code as confirmation."""
    if not current_user.totp_enabled:
        raise HTTPException(status_code=400, detail="2FA is not enabled")
    code_ok = _totp_valid(current_user.totp_secret, body.code) or _check_backup_code(current_user, body.code)
    if not code_ok:
        raise HTTPException(status_code=400, detail="Invalid code")
    current_user.totp_enabled = False
    current_user.totp_secret = None
    current_user.totp_backup_codes = None
    await db.commit()
    return {"2fa": "disabled"}


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    payload = decode_token(body.refresh_token, expected_type="refresh")
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    jti = payload.get("jti", "")
    if jti and await is_blacklisted(jti, db):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token has been revoked")

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

    new_access = create_access_token(str(user.id), token_version=user.token_version)
    new_refresh = create_refresh_token(str(user.id))

    # Rotate: blacklist the consumed refresh token
    if jti:
        await blacklist_token(jti, payload.get("exp", 0), db)

    await db.commit()
    return TokenResponse(access_token=new_access, refresh_token=new_refresh)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke the current access token by blacklisting its JTI."""
    payload = getattr(request.state, "token_payload", {})
    jti = payload.get("jti", "")
    exp = payload.get("exp", 0)
    if jti:
        await blacklist_token(jti, exp, db)
    await db.commit()
    await _signals.emit("post_logout", user=current_user, request=request)


@router.get("/me", response_model=UserPublic)
async def me(current_user: User = Depends(get_current_user)):
    return current_user


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

@router.get("/sessions", response_model=list[SessionRead])
async def list_sessions(
    request: Request,
    current_user: User = Depends(get_current_user),
):
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
    db: AsyncSession = Depends(get_db),
):
    record = next(
        (r for r in session_security._sessions.values() if r.jti == jti), None
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if str(record.user_id) != str(current_user.id) and not current_user.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    session_security.revoke(jti)
    await blacklist_token(jti, record.expires_at.timestamp(), db)
    await db.commit()


@router.post("/step-up", status_code=status.HTTP_200_OK)
async def step_up(
    request: Request,
    current_user: User = Depends(get_current_user),
):
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


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------

@router.post("/password-reset/request", status_code=status.HTTP_204_NO_CONTENT)
async def password_reset_request(request: Request, body: PasswordResetRequest, db: AsyncSession = Depends(get_db)):
    if not settings.PASSWORD_RESET_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if user is None:
        return  # don't reveal whether email exists

    import secrets as _secrets
    from adminfoundry.models.password_reset_token import PasswordResetToken
    from adminfoundry.email import send_email

    token = _secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.PASSWORD_RESET_TIMEOUT_MINUTES)
    db.add(PasswordResetToken(token=token, user_id=user.id, expires_at=expires_at))
    await db.commit()

    base_url = str(request.base_url).rstrip("/")
    reset_url = f"{base_url}{settings.ADMIN_UI_PATH}/password-reset/confirm?token={token}"
    await send_email(
        to=user.email,
        subject="Password reset",
        body_text=f"Reset your password: {reset_url}\n\nThis link expires in {settings.PASSWORD_RESET_TIMEOUT_MINUTES} minutes.",
        body_html=f'<p>Reset your password: <a href="{reset_url}">{reset_url}</a></p><p>Expires in {settings.PASSWORD_RESET_TIMEOUT_MINUTES} minutes.</p>',
    )


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def password_reset_confirm(body: PasswordResetConfirm, db: AsyncSession = Depends(get_db)):
    if not settings.PASSWORD_RESET_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    from adminfoundry.models.password_reset_token import PasswordResetToken
    from adminfoundry.auth import hash_password

    result = await db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token == body.token)
    )
    record = result.scalar_one_or_none()

    if record is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired token")
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if record.used or expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired token")

    user_result = await db.execute(select(User).where(User.id == record.user_id))
    user = user_result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired token")

    user.hashed_password = hash_password(body.new_password)
    record.used = True
    await db.commit()
