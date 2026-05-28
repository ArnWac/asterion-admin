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
from sqlalchemy.ext.asyncio import AsyncSession

from adminfoundry.audit import (
    TWO_FACTOR_DISABLED,
    TWO_FACTOR_ENABLED,
    record_audit_in_session,
    request_audit_kwargs,
)
from adminfoundry.auth.dependencies import get_current_user
from adminfoundry.auth.schemas import (
    TwoFactorDisableBody,
    TwoFactorEnableBody,
    TwoFactorEnableResponse,
    TwoFactorSetupResponse,
)
from adminfoundry.auth.totp import (
    clear_backup_codes,
    generate_backup_codes,
    generate_secret,
    provisioning_uri,
    store_backup_codes,
    verify_totp,
)
from adminfoundry.db.dependencies import get_async_session
from adminfoundry.models.user import User

router = APIRouter()


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

    issuer = request.app.state.adminfoundry.config.app_title
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
