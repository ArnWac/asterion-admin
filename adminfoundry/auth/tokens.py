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


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _create_token(
    payload: dict[str, Any],
    *,
    secret_key: str,
    algorithm: str,
    expires_delta: timedelta,
    token_type: str,
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
    )


def create_refresh_token(
    user_id: str | UUID,
    *,
    secret_key: str,
    algorithm: str,
    expires_minutes: int,
    token_version: int = 0,
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
    )


def decode_refresh_token(
    token: str,
    *,
    secret_key: str,
    algorithm: str,
) -> dict[str, Any]:
    """Decode + type-check a refresh token. Raises :class:`TokenError`
    for any non-refresh token so an access token can't be replayed at
    the refresh endpoint."""
    payload = decode_token(token, secret_key=secret_key, algorithm=algorithm)
    if payload.get("type") != REFRESH_TOKEN_TYPE:
        raise TokenError("Invalid token type")
    return payload


def is_refresh_token(payload: dict[str, Any]) -> bool:
    return payload.get("type") == REFRESH_TOKEN_TYPE


def create_impersonation_token(
    target_user_id: str | UUID,
    *,
    impersonated_by_user_id: str | UUID,
    tenant_id: str | UUID | None,
    secret_key: str,
    algorithm: str,
    expires_minutes: int = 60,
    token_version: int = 0,
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
    )


def decode_token(
    token: str,
    *,
    secret_key: str,
    algorithm: str,
) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            secret_key,
            algorithms=[algorithm],
        )
    except JWTError as exc:
        raise TokenError("Invalid token") from exc


def decode_access_token(
    token: str,
    *,
    secret_key: str,
    algorithm: str,
    allow_impersonation: bool = True,
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
