"""2FA enrollment endpoints (Roadmap 3.4a).

    POST /auth/2fa/setup    → generate a (pending) secret + provisioning URI
    POST /auth/2fa/enable   → verify first code, activate, return backup codes
    POST /auth/2fa/disable  → verify code, deactivate + clear backup codes

All three require an authenticated user (``get_current_user``). The
login step-up that actually demands a code at sign-in is 3.4b.

2FA is a builtin-User concept: an external auth provider handles its
own MFA at the IdP, so these endpoints operate directly on the builtin
``User`` model.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asterion.audit import (
    LOGIN_FAILURE,
    LOGIN_SUCCESS,
    TWO_FACTOR_DISABLED,
    TWO_FACTOR_ENABLED,
    record_audit,
    record_audit_in_session,
    request_audit_kwargs,
)
from asterion.auth.dependencies import get_current_user
from asterion.auth.rate_limiter import InMemoryLoginRateLimiter
from asterion.auth.revocation import (
    is_token_revoked,
    revoke_token,
    token_exp_as_datetime,
)
from asterion.auth.schemas import (
    TokenResponse,
    TwoFactorDisableBody,
    TwoFactorEnableBody,
    TwoFactorEnableResponse,
    TwoFactorLoginBody,
    TwoFactorSetupResponse,
)
from asterion.auth.tokens import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_mfa_challenge_token,
    get_subject_user_id,
    get_token_jti,
    get_token_version,
)
from asterion.auth.totp import (
    clear_backup_codes,
    consume_backup_code,
    generate_backup_codes,
    generate_secret,
    provisioning_uri,
    store_backup_codes,
    verify_totp,
)
from asterion.db.dependencies import get_async_session
from asterion.models.user import User
from asterion.providers.base import AdminPrincipal

router = APIRouter()

#: Fallback throttle for the 2FA login step (Review R18). The password factor
#: is rate-limited in the parent auth router, but ``/2fa/login`` was not — so an
#: attacker who cleared factor one could brute-force the 6-digit TOTP within the
#: challenge token's TTL. We bound the number of second-factor attempts *per
#: user* (not per challenge: a valid password can mint a fresh challenge at
#: will, so a per-challenge counter would be trivially reset). A shared backend
#: injected as ``runtime.login_rate_limiter`` (e.g. Redis) wins over this
#: in-process default so multi-worker deployments throttle across processes.
_mfa_limiter = InMemoryLoginRateLimiter()


@router.post("/2fa/setup", response_model=TwoFactorSetupResponse)
async def two_factor_setup(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> TwoFactorSetupResponse:
    """Begin 2FA enrollment: generate a secret (stored as *pending* —
    ``totp_enabled`` stays False) and return it + a provisioning URI for
    QR enrollment. Not active until ``/2fa/enable`` verifies a code.

    Calling setup again before enabling regenerates the secret, so a
    half-finished enrollment can be restarted cleanly.
    """
    if current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="2FA is already enabled. Disable it first to re-enroll.",
        )

    secret = generate_secret()
    current_user.totp_secret = secret
    await session.flush()

    issuer = request.app.state.asterion.config.app_title
    uri = provisioning_uri(secret, account_name=current_user.email, issuer=issuer)
    return TwoFactorSetupResponse(secret=secret, provisioning_uri=uri)


@router.post("/2fa/enable", response_model=TwoFactorEnableResponse)
async def two_factor_enable(
    payload: TwoFactorEnableBody,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> TwoFactorEnableResponse:
    """Activate 2FA by verifying the first code against the pending
    secret. On success, generate + return one-time backup codes (shown
    exactly once) and set ``totp_enabled=True``."""
    if current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="2FA is already enabled.",
        )
    if not current_user.totp_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No pending 2FA setup. Call /2fa/setup first.",
        )
    if not verify_totp(current_user.totp_secret, payload.code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid 2FA code.",
        )

    current_user.totp_enabled = True
    await session.flush()

    # Fresh backup codes — clear any leftovers from a prior enrollment.
    await clear_backup_codes(session, user_id=current_user.id)
    codes = generate_backup_codes()
    await store_backup_codes(session, user_id=current_user.id, codes=codes)

    await record_audit_in_session(
        session,
        action=TWO_FACTOR_ENABLED,
        actor=current_user,
        changes={"backup_codes_issued": len(codes)},
        **request_audit_kwargs(request, status_code=status.HTTP_200_OK),
    )

    return TwoFactorEnableResponse(backup_codes=codes)


@router.post("/2fa/disable", status_code=status.HTTP_200_OK)
async def two_factor_disable(
    payload: TwoFactorDisableBody,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, str]:
    """Turn off 2FA after verifying a current TOTP code. Clears the
    secret + every backup code."""
    if not current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA is not enabled.",
        )
    if not verify_totp(current_user.totp_secret, payload.code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid 2FA code.",
        )

    current_user.totp_enabled = False
    current_user.totp_secret = None
    await session.flush()
    await clear_backup_codes(session, user_id=current_user.id)

    await record_audit_in_session(
        session,
        action=TWO_FACTOR_DISABLED,
        actor=current_user,
        **request_audit_kwargs(request, status_code=status.HTTP_200_OK),
    )

    return {"detail": "2FA disabled."}


async def _audit_mfa_login(
    request: Request,
    *,
    action: str,
    status_code: int,
    user: User | None,
    reason: str | None = None,
    factor: str | None = None,
) -> None:
    """Isolated-session audit row for the 2FA login step (mirror of
    ``_audit_login`` in the parent auth router — kept here so the 2FA
    flow has full audit granularity)."""
    changes: dict[str, str] = {}
    if reason is not None:
        changes["reason"] = reason
    if factor is not None:
        changes["factor"] = factor
    actor = AdminPrincipal(id=str(user.id), email=user.email) if user is not None else None
    await record_audit(
        request.app.state.asterion.db,
        action=action,
        actor=actor,
        changes=changes or None,
        **request_audit_kwargs(request, status_code=status_code),
    )


def _bad_challenge() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired MFA challenge.",
    )


@router.post("/2fa/login", response_model=TokenResponse)
async def two_factor_login(
    payload: TwoFactorLoginBody,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> TokenResponse:
    """Complete a 2FA login: exchange the challenge token (from
    ``/auth/login``) + a TOTP code or a backup code for the real
    access+refresh pair (Roadmap 3.4b).

    Exactly one of ``code`` / ``backup_code`` must be provided. Backup
    codes are single-use — verified + marked used in the same TX. On
    success the challenge token's ``jti`` is revoked so it can't be
    replayed (single-use challenge).
    """
    # Exactly-one-of validation.
    has_code = bool(payload.code)
    has_backup = bool(payload.backup_code)
    if has_code == has_backup:  # both true or both false
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide exactly one of 'code' or 'backup_code'.",
        )

    runtime = request.app.state.asterion
    config = runtime.config
    # A shared backend (Review R7) wins over the in-memory default so
    # multi-worker deployments throttle the second factor across processes.
    limiter = getattr(runtime, "login_rate_limiter", None) or _mfa_limiter

    try:
        challenge_payload = decode_mfa_challenge_token(
            payload.mfa_token,
            secret_key=config.secret_key,
            algorithm=config.jwt_algorithm,
            issuer=config.jwt_issuer,
            audience=config.jwt_audience,
        )
        user_id = get_subject_user_id(challenge_payload)
        token_version = get_token_version(challenge_payload)
        challenge_jti = get_token_jti(challenge_payload)
    except TokenError as exc:
        raise _bad_challenge() from exc

    if await is_token_revoked(session, challenge_jti):
        await _audit_mfa_login(
            request,
            action=LOGIN_FAILURE,
            status_code=status.HTTP_401_UNAUTHORIZED,
            user=None,
            reason="challenge_revoked",
        )
        raise _bad_challenge()

    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise _bad_challenge()
    if user.token_version != token_version:
        # logout-all since the challenge was issued — invalidate it.
        raise _bad_challenge()
    if not user.totp_enabled:
        # Edge case: 2FA was disabled between /login and /2fa/login.
        # Reject — caller should re-do /login (which will now give them
        # tokens directly).
        raise _bad_challenge()

    # Throttle second-factor attempts per user (Review R18). Without this an
    # attacker holding a valid challenge could brute-force the 6-digit TOTP
    # before it expires. Keyed on the user, not the challenge jti, so minting a
    # fresh challenge via re-login does not reset the counter.
    mfa_key = f"mfa:{user.id}"
    if await limiter.is_limited(mfa_key):
        await _audit_mfa_login(
            request,
            action=LOGIN_FAILURE,
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            user=user,
            reason="mfa_rate_limited",
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many 2FA attempts. Try again later.",
        )

    # Verify the second factor.
    if has_code:
        if not verify_totp(user.totp_secret, payload.code or ""):
            await limiter.record_failure(mfa_key)
            await _audit_mfa_login(
                request,
                action=LOGIN_FAILURE,
                status_code=status.HTTP_401_UNAUTHORIZED,
                user=user,
                reason="invalid_totp",
                factor="totp",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid 2FA code.",
            )
        factor = "totp"
    else:
        ok = await consume_backup_code(session, user_id=user.id, raw_code=payload.backup_code or "")
        if not ok:
            await limiter.record_failure(mfa_key)
            await _audit_mfa_login(
                request,
                action=LOGIN_FAILURE,
                status_code=status.HTTP_401_UNAUTHORIZED,
                user=user,
                reason="invalid_backup_code",
                factor="backup_code",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid backup code.",
            )
        factor = "backup_code"

    # Second factor passed — reset the attempt counter for this user.
    await limiter.clear(mfa_key)

    # Single-use challenge — revoke its jti so a replay (e.g. with a
    # leaked challenge token + a phished code) is rejected.
    await revoke_token(
        session,
        jti=challenge_jti,
        user_id=user.id,
        expires_at=token_exp_as_datetime(challenge_payload),
        reason="mfa_challenge_consumed",
    )

    access = create_access_token(
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

    await _audit_mfa_login(
        request,
        action=LOGIN_SUCCESS,
        status_code=200,
        user=user,
        factor=factor,
    )

    return TokenResponse(access_token=access, refresh_token=refresh)
