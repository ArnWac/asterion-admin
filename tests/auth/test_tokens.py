"""Tests for JWT token creation and decoding."""

from __future__ import annotations

import uuid

import pytest

from asterion.auth.tokens import (
    TokenError,
    create_access_token,
    decode_access_token,
    get_subject_user_id,
    get_token_version,
    is_impersonation_token,
)

SECRET = "test-secret"
ALGO = "HS256"


def _make_token(user_id=None, token_version=1, **kwargs):
    uid = user_id or uuid.uuid4()
    return create_access_token(
        uid,
        secret_key=SECRET,
        algorithm=ALGO,
        expires_minutes=60,
        token_version=token_version,
        **kwargs,
    ), uid


def test_create_and_decode_token():
    token, uid = _make_token()
    payload = decode_access_token(token, secret_key=SECRET, algorithm=ALGO)
    assert get_subject_user_id(payload) == uid


def test_get_token_version():
    token, _ = _make_token(token_version=7)
    payload = decode_access_token(token, secret_key=SECRET, algorithm=ALGO)
    assert get_token_version(payload) == 7


def test_wrong_secret_raises_token_error():
    token, _ = _make_token()
    with pytest.raises(TokenError):
        decode_access_token(token, secret_key="wrong-secret", algorithm=ALGO)


def test_invalid_token_raises_token_error():
    with pytest.raises(TokenError):
        decode_access_token("not.a.token", secret_key=SECRET, algorithm=ALGO)


def test_normal_token_is_not_impersonation():
    token, _ = _make_token()
    payload = decode_access_token(token, secret_key=SECRET, algorithm=ALGO)
    assert is_impersonation_token(payload) is False
