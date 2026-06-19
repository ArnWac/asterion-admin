"""Tests for auth dependency behaviour (impersonation guard, superadmin check)."""

from __future__ import annotations

import uuid

from asterion.auth.tokens import create_access_token, is_impersonation_token

SECRET = "test-secret"
ALGO = "HS256"


def _make_token(user_id=None, impersonated_by=None):
    uid = user_id or uuid.uuid4()
    if impersonated_by:
        from asterion.auth.tokens import create_impersonation_token

        return create_impersonation_token(
            uid,
            impersonated_by_user_id=uuid.uuid4(),
            tenant_id=None,
            secret_key=SECRET,
            algorithm=ALGO,
            expires_minutes=60,
            token_version=1,
        )
    return create_access_token(
        uid,
        secret_key=SECRET,
        algorithm=ALGO,
        expires_minutes=60,
        token_version=1,
    )


def test_normal_token_not_impersonation():
    token = _make_token()
    from asterion.auth.tokens import decode_access_token

    payload = decode_access_token(token, secret_key=SECRET, algorithm=ALGO)
    assert is_impersonation_token(payload) is False


def test_impersonation_token_flagged():
    token = _make_token(impersonated_by=True)
    from asterion.auth.tokens import decode_access_token

    payload = decode_access_token(token, secret_key=SECRET, algorithm=ALGO)
    assert is_impersonation_token(payload) is True
