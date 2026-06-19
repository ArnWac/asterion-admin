from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asterion.audit import (
    LOGIN_FAILURE,
    LOGIN_SUCCESS,
    LOGOUT,
    LOGOUT_ALL,
    PASSWORD_RESET_CONFIRM,
    PASSWORD_RESET_REQUEST,
    record_audit,
    record_audit_in_session,
    request_audit_kwargs,
)
from asterion.auth.dependencies import get_current_user
from asterion.auth.password import hash_password, validate_password_strength
from asterion.auth.password_reset import (
    consume_password_reset,
    create_password_reset,
)
from asterion.auth.rate_limiter import InMemoryLoginRateLimiter
from asterion.auth.revocation import (
    is_token_revoked,
    revoke_token,
    token_exp_as_datetime,
)
from asterion.auth.schemas import (
    LoginRequest,
    MeResponse,
    PasswordResetConfirmBody,
    PasswordResetRequestBody,
    RefreshRequest,
    TokenResponse,
)
from asterion.auth.tokens import (
    TokenError,
    create_access_token,
    create_mfa_challenge_token,
    create_refresh_token,
    decode_refresh_token,
    get_subject_user_id,
    get_token_jti,
    get_token_version,
)
from asterion.core.net import request_client_ip
from asterion.db.dependencies import get_async_session
from asterion.models.user import User
from asterion.providers.base import (
    AdminPrincipal,
    LoginCredentials,
    LoginError,
)

router = APIRouter()

_login_limiter = InMemoryLoginRateLimiter()

#: ``LoginError.reason`` → (HTTP status, client-facing detail). Unknown
#: reasons fall back to 401 so a custom provider's bespoke reason never
#: leaks as a 500.
_LOGIN_FAILURE_STATUS: dict[str, tuple[int, str]] = {
    "invalid_credentials": (status.HTTP_401_UNAUTHORIZED, "Invalid credentials."),
    "inactive_user": (status.HTTP_403_FORBIDDEN, "User is inactive."),
}


async def _audit_login(
    request: Request,
    *,
    action: str,
    status_code: int,
    email: str,
    actor: AdminPrincipal | None = None,
    reason: str | None = None,
) -> None:
    """Audit a login attempt using an isolated session.

    Login uses an isolated audit session (not the request session) so the
    audit row commits even when the request raises (rate-limit / bad
    credentials / inactive user). Safe on SQLite because the login request
    session has only issued a SELECT — no writer lock to contend with.
    """
    changes: dict[str, str] = {"email": email}
    if reason is not None:
        changes["reason"] = reason
    await record_audit(
        request.app.state.asterion.db,
        action=action,
        actor=actor,
        changes=changes,
        **request_audit_kwargs(request, status_code=status_code),
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> TokenResponse:
    """Password login.

    Roadmap 2.6: credential verification + token minting are delegated
    to the configured auth provider's ``login`` (the
    :class:`CredentialAuthProvider` surface). This route keeps the
    transport concerns — rate-limiting, audit with per-reason
    granularity, and HTTP-status mapping — so a custom provider stays
    transport-agnostic. A provider that can't do password login (e.g.
    a pure OAuth/OIDC provider) makes this endpoint return 501.
    """
    runtime = request.app.state.asterion
    # Limiter key (Review R15): email by default. When ``login_rate_limit_by_ip``
    # is on, scope it to ``(email, ip)`` using the trusted client IP (R16) so a
    # single source can't lock a victim out of every other client — at the cost
    # of a per-IP reset, which is why it is opt-in.
    email_key = payload.email.lower()
    if getattr(runtime.config, "login_rate_limit_by_ip", False):
        limiter_key = f"{email_key}|{request_client_ip(request) or 'unknown'}"
    else:
        limiter_key = email_key
    # A shared backend (Review R7) wins over the in-memory default so
    # multi-worker deployments throttle across processes.
    limiter = getattr(runtime, "login_rate_limiter", None) or _login_limiter
    provider = runtime.providers.auth

    login_fn = getattr(provider, "login", None)
    if login_fn is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="The configured auth provider does not support password login.",
        )

    if await limiter.is_limited(limiter_key):
        await _audit_login(
            request,
            action=LOGIN_FAILURE,
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            email=payload.email,
            reason="rate_limited",
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts.",
        )

    try:
        session_result = await login_fn(
            LoginCredentials(email=payload.email, password=payload.password),
            request=request,
        )
    except LoginError as exc:
        await limiter.record_failure(limiter_key)
        http_status, detail = _LOGIN_FAILURE_STATUS.get(
            exc.reason, (status.HTTP_401_UNAUTHORIZED, "Invalid credentials.")
        )
        await _audit_login(
            request,
            action=LOGIN_FAILURE,
            status_code=http_status,
            email=payload.email,
            reason=exc.reason,
        )
        raise HTTPException(status_code=http_status, detail=detail) from exc

    await limiter.clear(limiter_key)

    # 2FA step-up (Roadmap 3.4b). Builtin-only concern — the User
    # model carries totp_enabled. External auth providers handle MFA
    # at the IdP, so when the subject doesn't map to a builtin user
    # we just hand back the token pair the provider minted.
    user_for_mfa: User | None = None
    if session_result.subject is not None:
        try:
            uuid_subject = uuid.UUID(session_result.subject)
            user_for_mfa = (
                await session.execute(select(User).where(User.id == uuid_subject))
            ).scalar_one_or_none()
        except (ValueError, AttributeError):
            user_for_mfa = None

    if user_for_mfa is not None and user_for_mfa.totp_enabled:
        config = request.app.state.asterion.config
        challenge = create_mfa_challenge_token(
            user_for_mfa.id,
            secret_key=config.secret_key,
            algorithm=config.jwt_algorithm,
            token_version=user_for_mfa.token_version,
            issuer=config.jwt_issuer,
            audience=config.jwt_audience,
        )
        # Audit at LOGIN_SUCCESS — the password factor passed; the
        # second-factor outcome will produce its own audit row at
        # /auth/2fa/login.
        await _audit_login(
            request,
            action=LOGIN_SUCCESS,
            status_code=200,
            email=payload.email,
            actor=AdminPrincipal(id=session_result.subject, email=payload.email),
            reason="mfa_required",
        )
        return TokenResponse(
            access_token=None,
            refresh_token=None,
            mfa_required=True,
            mfa_token=challenge,
        )

    actor = (
        AdminPrincipal(id=session_result.subject, email=payload.email)
        if session_result.subject is not None
        else None
    )
    await _audit_login(
        request,
        action=LOGIN_SUCCESS,
        status_code=200,
        email=payload.email,
        actor=actor,
    )

    return TokenResponse(
        access_token=session_result.access_token,
        token_type=session_result.token_type,
        refresh_token=session_result.refresh_token,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> TokenResponse:
    """Exchange a refresh token for a fresh access+refresh pair (Roadmap 3.1).

    Rotation: the presented refresh token's ``jti`` is revoked and a new
    refresh token is issued, so a refresh token is single-use. A replayed
    (already-rotated) refresh token is rejected because its jti is now in
    ``revoked_tokens``.

    Validates signature, ``type=refresh``, the ``tkv`` invariant (a
    ``/logout-all`` since issuance invalidates it), the per-jti
    revocation store, and that the user still exists + is active.
    """
    config = request.app.state.asterion.config

    try:
        token_payload = decode_refresh_token(
            payload.refresh_token,
            secret_key=config.secret_key,
            algorithm=config.jwt_algorithm,
            issuer=config.jwt_issuer,
            audience=config.jwt_audience,
        )
        user_id = get_subject_user_id(token_payload)
        token_version = get_token_version(token_payload)
        jti = get_token_jti(token_payload)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if await is_token_revoked(session, jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked.",
        )

    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token.",
        )
    if user.token_version != token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked.",
        )

    # Rotation: revoke the presented refresh token so it can't be reused.
    await revoke_token(
        session,
        jti=jti,
        user_id=user.id,
        expires_at=token_exp_as_datetime(token_payload),
        reason="refresh_rotation",
    )

    new_access = create_access_token(
        user.id,
        secret_key=config.secret_key,
        algorithm=config.jwt_algorithm,
        expires_minutes=config.access_token_expire_minutes,
        token_version=user.token_version,
        issuer=config.jwt_issuer,
        audience=config.jwt_audience,
    )
    new_refresh = create_refresh_token(
        user.id,
        secret_key=config.secret_key,
        algorithm=config.jwt_algorithm,
        expires_minutes=config.refresh_token_expire_minutes,
        token_version=user.token_version,
        issuer=config.jwt_issuer,
        audience=config.jwt_audience,
    )
    return TokenResponse(access_token=new_access, refresh_token=new_refresh)


@router.get("/me", response_model=MeResponse)
async def me(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> MeResponse:
    return MeResponse(
        id=str(current_user.id),
        email=current_user.email,
        full_name=current_user.full_name,
        is_active=current_user.is_active,
        is_superadmin=current_user.is_superadmin,
        is_impersonating=bool(getattr(request.state, "is_impersonating", False)),
    )


@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, str]:
    """Revoke ONLY the access token used for this request (Roadmap 3.2).

    Records the token's ``jti`` in ``revoked_tokens``; both auth paths
    reject a revoked jti on subsequent requests. Other sessions for the
    same user keep working — use ``/logout-all`` to invalidate every
    session at once.

    Idempotent: logging out a token whose jti is already revoked
    returns 200 without writing a duplicate row.
    """
    payload = getattr(request.state, "token_payload", {}) or {}
    jti = get_token_jti(payload)
    newly_revoked = await revoke_token(
        session,
        jti=jti,
        user_id=current_user.id,
        expires_at=token_exp_as_datetime(payload),
        reason="logout",
    )

    await record_audit_in_session(
        session,
        action=LOGOUT,
        actor=current_user,
        changes={"jti": jti, "newly_revoked": newly_revoked},
        **request_audit_kwargs(request, status_code=status.HTTP_200_OK),
    )

    return {"detail": "Session invalidated."}


@router.post("/logout-all", status_code=status.HTTP_200_OK)
async def logout_all(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, str]:
    """Revoke every access token previously issued to the current user.

    Implementation: bumps ``User.token_version``. Every token in the wild
    carries a ``tkv`` claim; ``get_current_user`` rejects with 401 when
    they no longer match. Single-token logout is ``/logout`` (per-jti
    revocation via ``revoked_tokens``).
    """
    current_user.token_version = (current_user.token_version or 0) + 1
    await session.flush()

    await record_audit_in_session(
        session,
        action=LOGOUT_ALL,
        actor=current_user,
        # Key name avoids "token" / "secret" so the sanitizer leaves it alone.
        changes={"bumped_to": current_user.token_version},
        **request_audit_kwargs(request, status_code=status.HTTP_200_OK),
    )

    return {"detail": "All sessions invalidated."}


@router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
async def password_reset_request(
    payload: PasswordResetRequestBody,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, str]:
    """Begin a password reset (Roadmap 3.3).

    ALWAYS returns 202 regardless of whether the email maps to a known,
    active user — this prevents account enumeration. When the email does
    match an active user, a single-use token is generated, its hash
    stored, and the raw token handed to the configured
    ``PasswordResetNotifier`` for delivery.
    """
    runtime = request.app.state.asterion
    config = runtime.config

    user = (
        await session.execute(select(User).where(User.email == payload.email))
    ).scalar_one_or_none()

    issued = False
    if user is not None and user.is_active:
        raw_token = await create_password_reset(
            session,
            user=user,
            ttl_minutes=config.password_reset_token_expire_minutes,
        )
        notifier = runtime.password_reset_notifier
        if notifier is not None:
            await notifier.send_reset(email=payload.email, token=raw_token, request=request)
        issued = True

    await record_audit_in_session(
        session,
        action=PASSWORD_RESET_REQUEST,
        actor=user if issued else None,
        changes={"email": payload.email, "issued": issued},
        **request_audit_kwargs(request, status_code=status.HTTP_202_ACCEPTED),
    )

    # Identical response on both branches — no enumeration signal.
    return {"detail": "If the account exists, a reset link has been sent."}


@router.post("/password-reset/confirm", status_code=status.HTTP_200_OK)
async def password_reset_confirm(
    payload: PasswordResetConfirmBody,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, str]:
    """Complete a password reset (Roadmap 3.3).

    Verifies + consumes the single-use token, sets the new password, and
    bumps ``token_version`` so every existing session for the user is
    invalidated. Returns 400 for an invalid / expired / already-used
    token without revealing which.
    """
    config = request.app.state.asterion.config

    try:
        validate_password_strength(payload.new_password, min_length=config.password_min_length)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    user = await consume_password_reset(session, raw_token=payload.token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token.",
        )

    user.hashed_password = hash_password(payload.new_password)
    # Invalidate every existing session — a reset implies the old
    # credentials may be compromised.
    user.token_version = (user.token_version or 0) + 1
    await session.flush()

    await record_audit_in_session(
        session,
        action=PASSWORD_RESET_CONFIRM,
        actor=user,
        changes={"bumped_to": user.token_version},
        **request_audit_kwargs(request, status_code=status.HTTP_200_OK),
    )

    return {"detail": "Password updated."}
