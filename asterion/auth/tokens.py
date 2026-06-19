from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from jose import JWTError, jwt


class TokenError(Exception):
    """Raised when a JWT is invalid or does not match the expected contract."""


ACCESS_TOKEN_TYPE = "access"
IMPERSONATION_TOKEN_TYPE = "impersonation"
REFRESH_TOKEN_TYPE = "refresh"
MFA_CHALLENGE_TOKEN_TYPE = "mfa_challenge"


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _create_token(
    payload: dict[str, Any],
    *,
    secret_key: str,
    algorithm: str,
    expires_delta: timedelta,
    token_type: str,
    issuer: str | None = None,
    audience: str | None = None,
) -> str:
    now = _now_utc()

    token_payload = payload.copy()
    token_payload.update(
        {
            "iat": int(now.timestamp()),
            "exp": now + expires_delta,
            "jti": str(uuid.uuid4()),
            "type": token_type,
        }
    )
    # Review R8: stamp iss/aud only when the deployment configured them, so
    # the default (None) keeps the historical claim-free token shape.
    if issuer is not None:
        token_payload["iss"] = issuer
    if audience is not None:
        token_payload["aud"] = audience

    return jwt.encode(
        token_payload,
        secret_key,
        algorithm=algorithm,
    )


def create_access_token(
    user_id: str | UUID,
    *,
    secret_key: str,
    algorithm: str,
    expires_minutes: int,
    token_version: int = 0,
    issuer: str | None = None,
    audience: str | None = None,
) -> str:
    """
    Create a normal user access token.

    Payload:
        sub: user id
        tkv: token version
        type: access
        jti: unique token id
        iat: issued-at timestamp
        exp: expiry
    """
    return _create_token(
        {
            "sub": str(user_id),
            "tkv": token_version,
        },
        secret_key=secret_key,
        algorithm=algorithm,
        expires_delta=timedelta(minutes=expires_minutes),
        token_type=ACCESS_TOKEN_TYPE,
        issuer=issuer,
        audience=audience,
    )


def create_refresh_token(
    user_id: str | UUID,
    *,
    secret_key: str,
    algorithm: str,
    expires_minutes: int,
    token_version: int = 0,
    issuer: str | None = None,
    audience: str | None = None,
) -> str:
    """Create a long-lived refresh token (Roadmap 3.1).

    Same ``sub`` / ``tkv`` / ``jti`` shape as an access token but
    ``type="refresh"`` and a longer expiry. Exchanged at
    ``/auth/refresh`` for a fresh access+refresh pair (the old refresh
    token's jti is revoked on each exchange — rotation).
    """
    return _create_token(
        {
            "sub": str(user_id),
            "tkv": token_version,
        },
        secret_key=secret_key,
        algorithm=algorithm,
        expires_delta=timedelta(minutes=expires_minutes),
        token_type=REFRESH_TOKEN_TYPE,
        issuer=issuer,
        audience=audience,
    )


def decode_refresh_token(
    token: str,
    *,
    secret_key: str,
    algorithm: str,
    issuer: str | None = None,
    audience: str | None = None,
) -> dict[str, Any]:
    """Decode + type-check a refresh token. Raises :class:`TokenError`
    for any non-refresh token so an access token can't be replayed at
    the refresh endpoint."""
    payload = decode_token(
        token, secret_key=secret_key, algorithm=algorithm, issuer=issuer, audience=audience
    )
    if payload.get("type") != REFRESH_TOKEN_TYPE:
        raise TokenError("Invalid token type")
    return payload


def is_refresh_token(payload: dict[str, Any]) -> bool:
    return payload.get("type") == REFRESH_TOKEN_TYPE


def create_mfa_challenge_token(
    user_id: str | UUID,
    *,
    secret_key: str,
    algorithm: str,
    expires_minutes: int = 5,
    token_version: int = 0,
    issuer: str | None = None,
    audience: str | None = None,
) -> str:
    """Short-lived token returned by ``/auth/login`` when the user has
    2FA enabled (Roadmap 3.4b).

    Carries only ``sub`` + ``tkv``; it does NOT authenticate any other
    request. The companion endpoint ``/auth/2fa/login`` exchanges it
    (plus a TOTP code or backup code) for the real access+refresh pair.
    Default TTL is 5 minutes — long enough for the user to grab their
    authenticator, short enough that a leaked challenge is mostly dead.
    """
    return _create_token(
        {
            "sub": str(user_id),
            "tkv": token_version,
        },
        secret_key=secret_key,
        algorithm=algorithm,
        expires_delta=timedelta(minutes=expires_minutes),
        token_type=MFA_CHALLENGE_TOKEN_TYPE,
        issuer=issuer,
        audience=audience,
    )


def decode_mfa_challenge_token(
    token: str,
    *,
    secret_key: str,
    algorithm: str,
    issuer: str | None = None,
    audience: str | None = None,
) -> dict[str, Any]:
    """Decode + type-check an MFA challenge token. Raises :class:`TokenError`
    for any other token type so a stale access token can't be replayed
    at ``/auth/2fa/login``."""
    payload = decode_token(
        token, secret_key=secret_key, algorithm=algorithm, issuer=issuer, audience=audience
    )
    if payload.get("type") != MFA_CHALLENGE_TOKEN_TYPE:
        raise TokenError("Invalid token type")
    return payload


def create_impersonation_token(
    target_user_id: str | UUID,
    *,
    impersonated_by_user_id: str | UUID,
    tenant_id: str | UUID | None,
    secret_key: str,
    algorithm: str,
    expires_minutes: int = 60,
    token_version: int = 0,
    issuer: str | None = None,
    audience: str | None = None,
) -> str:
    """
    Create a short-lived impersonation token.

    Rules:
    - subject is the target/impersonated user
    - impersonated_by identifies the superadmin/support user
    - token type is 'impersonation'
    - token is non-renewable by convention
    - optional tenant_id scopes the impersonation to one tenant context
    """
    payload: dict[str, Any] = {
        "sub": str(target_user_id),
        "tkv": token_version,
        "impersonated_by": str(impersonated_by_user_id),
        "renewable": False,
    }

    if tenant_id is not None:
        payload["tenant_id"] = str(tenant_id)

    return _create_token(
        payload,
        secret_key=secret_key,
        algorithm=algorithm,
        expires_delta=timedelta(minutes=expires_minutes),
        token_type=IMPERSONATION_TOKEN_TYPE,
        issuer=issuer,
        audience=audience,
    )


def decode_token(
    token: str,
    *,
    secret_key: str,
    algorithm: str,
    issuer: str | None = None,
    audience: str | None = None,
) -> dict[str, Any]:
    """Decode + verify a JWT.

    ``alg`` is pinned to ``algorithm`` and ``exp`` is enforced by the library.
    When ``issuer`` / ``audience`` are supplied (Review R8), the matching
    ``iss`` / ``aud`` claim is verified too — a token missing or mismatching a
    required claim raises :class:`TokenError`. Passing ``None`` (the default)
    skips that claim, preserving the historical behaviour.
    """
    # When a claim is configured it must be *present* — not just "valid if
    # present". jose only checks aud/iss when they exist unless told to
    # require them, so a token minted before the deployment required the claim
    # would otherwise still pass.
    options: dict[str, bool] = {}
    if audience is not None:
        options["require_aud"] = True
    if issuer is not None:
        options["require_iss"] = True
    try:
        return jwt.decode(
            token,
            secret_key,
            algorithms=[algorithm],
            issuer=issuer,
            audience=audience,
            options=options,
        )
    except JWTError as exc:
        raise TokenError("Invalid token") from exc


def decode_access_token(
    token: str,
    *,
    secret_key: str,
    algorithm: str,
    allow_impersonation: bool = True,
    issuer: str | None = None,
    audience: str | None = None,
) -> dict[str, Any]:
    """
    Decode an access-like token.

    By default this accepts:
    - normal access tokens
    - impersonation tokens

    Superadmin/root-only dependencies should explicitly reject impersonation
    with `is_impersonation_token(payload)`.
    """
    payload = decode_token(
        token,
        secret_key=secret_key,
        algorithm=algorithm,
        issuer=issuer,
        audience=audience,
    )

    token_type = payload.get("type")

    allowed_types = {ACCESS_TOKEN_TYPE}
    if allow_impersonation:
        allowed_types.add(IMPERSONATION_TOKEN_TYPE)

    if token_type not in allowed_types:
        raise TokenError("Invalid token type")

    return payload


def get_subject_user_id(payload: dict[str, Any]) -> UUID:
    subject = payload.get("sub")

    if not subject:
        raise TokenError("Missing token subject")

    try:
        return UUID(str(subject))
    except ValueError as exc:
        raise TokenError("Invalid token subject") from exc


def get_token_version(payload: dict[str, Any]) -> int:
    value = payload.get("tkv")

    if value is None:
        raise TokenError("Missing token version")

    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise TokenError("Invalid token version") from exc


def get_token_jti(payload: dict[str, Any]) -> str:
    jti = payload.get("jti")

    if not jti:
        raise TokenError("Missing token jti")

    return str(jti)


def get_impersonator_user_id(payload: dict[str, Any]) -> UUID | None:
    value = payload.get("impersonated_by")

    if not value:
        return None

    try:
        return UUID(str(value))
    except ValueError as exc:
        raise TokenError("Invalid impersonator user id") from exc


def get_impersonation_tenant_id(payload: dict[str, Any]) -> UUID | None:
    value = payload.get("tenant_id")

    if not value:
        return None

    try:
        return UUID(str(value))
    except ValueError as exc:
        raise TokenError("Invalid impersonation tenant id") from exc


def is_impersonation_token(payload: dict[str, Any]) -> bool:
    return payload.get("type") == IMPERSONATION_TOKEN_TYPE


def is_normal_access_token(payload: dict[str, Any]) -> bool:
    return payload.get("type") == ACCESS_TOKEN_TYPE
