"""JWT iss/aud hardening (Review R8).

Verifies that ``issuer`` / ``audience`` are stamped only when configured and
strictly validated on decode, while the default (None) preserves the historic
claim-free behaviour.
"""

from __future__ import annotations

import pytest

from asterion.auth.tokens import (
    TokenError,
    create_access_token,
    decode_access_token,
    decode_token,
)

SECRET = "unit-test-secret"
ALG = "HS256"


def _make(**kw) -> str:
    return create_access_token(
        "11111111-1111-1111-1111-111111111111",
        secret_key=SECRET,
        algorithm=ALG,
        expires_minutes=5,
        **kw,
    )


def test_default_tokens_carry_no_iss_or_aud():
    payload = decode_token(_make(), secret_key=SECRET, algorithm=ALG)
    assert "iss" not in payload
    assert "aud" not in payload


def test_configured_iss_aud_round_trip():
    token = _make(issuer="asterion", audience="admin-ui")
    payload = decode_access_token(
        token, secret_key=SECRET, algorithm=ALG, issuer="asterion", audience="admin-ui"
    )
    assert payload["iss"] == "asterion"
    assert payload["aud"] == "admin-ui"


def test_wrong_audience_is_rejected():
    token = _make(issuer="asterion", audience="admin-ui")
    with pytest.raises(TokenError):
        decode_access_token(
            token, secret_key=SECRET, algorithm=ALG, issuer="asterion", audience="other-app"
        )


def test_wrong_issuer_is_rejected():
    token = _make(issuer="asterion", audience="admin-ui")
    with pytest.raises(TokenError):
        decode_access_token(
            token, secret_key=SECRET, algorithm=ALG, issuer="someone-else", audience="admin-ui"
        )


def test_token_without_aud_is_rejected_when_audience_required():
    """Rollout invariant: once a deployment requires an audience, a token that
    predates the config (no ``aud``) must not validate."""
    token = _make()  # no aud
    with pytest.raises(TokenError):
        decode_access_token(token, secret_key=SECRET, algorithm=ALG, audience="admin-ui")


def test_unconfigured_decode_ignores_present_iss():
    """An ``iss`` in the token but no issuer expectation on decode is fine —
    iss is informational unless explicitly required."""
    token = _make(issuer="asterion")
    payload = decode_access_token(token, secret_key=SECRET, algorithm=ALG)
    assert payload["iss"] == "asterion"
