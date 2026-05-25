"""Tests for the sealed-cookie state/PKCE module — Phase 8b.3.

These tests are deliberately heavy on the failure paths. Each one
corresponds to an attack the sealed cookie has to survive:

* tampered payload (attacker flipped a bit, kept the old signature)
* tampered signature (attacker flipped the signature instead)
* wrong secret (attacker has a leaked old key from a previous deploy)
* schema mismatch (attacker forged a payload with missing/extra fields)
* expired cookie (replay of a long-stale callback URL)
* future-dated cookie (forged timestamp)
* missing / malformed cookie (no dot, empty, garbage)

If any of these silently succeed, the whole OAuth flow collapses, so
the tests assert *behaviour under attack*, not just happy-path
round-trips.
"""

from __future__ import annotations

import base64
import json
import re
import time

import pytest

from adminfoundry.extensions.auth_oauth.state import (
    COOKIE_NAME_INSECURE,
    COOKIE_NAME_SECURE,
    STATE_TTL_SECONDS,
    OAuthFlowState,
    OAuthStateExpiredError,
    OAuthStateInvalidError,
    code_challenge_from_verifier,
    cookie_name_for_request,
    generate_code_verifier,
    generate_nonce,
    generate_state,
    seal_state,
    unseal_state,
)

_SECRET = "test-secret-for-state-cookie-tests"


def _make_state(**overrides) -> OAuthFlowState:
    base = {
        "state": "abc",
        "code_verifier": "def",
        "nonce": "ghi",
        "provider_id": "google",
        "return_to": "/admin/dashboard",
        "created_at": int(time.time()),
    }
    base.update(overrides)
    return OAuthFlowState(**base)


# --- generators ---


def test_generate_state_has_high_entropy():
    """Two calls must never collide — 256-bit token has ~zero collision risk."""
    samples = {generate_state() for _ in range(100)}
    assert len(samples) == 100


def test_generate_state_is_url_safe_base64():
    s = generate_state()
    # token_urlsafe characters: A-Z, a-z, 0-9, '-', '_'. No padding.
    assert re.fullmatch(r"[A-Za-z0-9_-]+", s)
    assert "=" not in s


def test_generate_nonce_has_high_entropy():
    samples = {generate_nonce() for _ in range(100)}
    assert len(samples) == 100


def test_generate_code_verifier_meets_rfc7636_length():
    """RFC 7636 §4.1 requires 43-128 chars in the [A-Z][a-z][0-9]-._~ set.
    token_urlsafe(64) produces ~86 chars from a subset of those — well
    inside the bounds."""
    v = generate_code_verifier()
    assert 43 <= len(v) <= 128
    assert re.fullmatch(r"[A-Za-z0-9_-]+", v)


# --- PKCE challenge ---


def test_code_challenge_matches_rfc7636_appendix_b():
    """RFC 7636 Appendix B test vector — known-answer for the SHA-256
    derivation. If this drifts, every IdP rejects our auth requests."""
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    expected = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    assert code_challenge_from_verifier(verifier) == expected


# --- seal/unseal round-trip ---


def test_round_trip_recovers_payload():
    payload = _make_state(state="state-xyz", nonce="nonce-xyz")
    sealed = seal_state(payload, _SECRET)
    assert "." in sealed
    out = unseal_state(sealed, _SECRET)
    assert out == payload


def test_round_trip_preserves_all_fields():
    payload = _make_state(
        state="STATE",
        code_verifier="VERIFIER",
        nonce="NONCE",
        provider_id="google_workspace",
        return_to="/admin/widgets/42/edit",
    )
    out = unseal_state(seal_state(payload, _SECRET), _SECRET)
    assert out.state == "STATE"
    assert out.code_verifier == "VERIFIER"
    assert out.nonce == "NONCE"
    assert out.provider_id == "google_workspace"
    assert out.return_to == "/admin/widgets/42/edit"


# --- failure paths ---


def test_tampered_payload_rejected():
    """Attacker flips a bit in the payload — signature no longer matches."""
    sealed = seal_state(_make_state(), _SECRET)
    body, _, sig = sealed.rpartition(".")
    # Swap a character in the b64 payload.
    swapped = body[:-1] + ("A" if body[-1] != "A" else "B")
    with pytest.raises(OAuthStateInvalidError, match="signature mismatch"):
        unseal_state(f"{swapped}.{sig}", _SECRET)


def test_tampered_signature_rejected():
    sealed = seal_state(_make_state(), _SECRET)
    body, _, sig = sealed.rpartition(".")
    # Flip last character of the signature.
    swapped_sig = sig[:-1] + ("A" if sig[-1] != "A" else "B")
    with pytest.raises(OAuthStateInvalidError, match="signature mismatch"):
        unseal_state(f"{body}.{swapped_sig}", _SECRET)


def test_wrong_secret_rejected():
    """Leaked old secret from a prior deploy must not validate new cookies."""
    sealed = seal_state(_make_state(), _SECRET)
    with pytest.raises(OAuthStateInvalidError, match="signature mismatch"):
        unseal_state(sealed, "different-secret")


def test_missing_dot_rejected():
    with pytest.raises(OAuthStateInvalidError, match="missing or malformed"):
        unseal_state("garbage-with-no-dot", _SECRET)


def test_empty_cookie_rejected():
    with pytest.raises(OAuthStateInvalidError, match="missing or malformed"):
        unseal_state("", _SECRET)


def test_undecodable_payload_rejected():
    """Body is dot-separated but not valid b64url — should not crash, should raise."""
    # Build a signed cookie where the body is not valid b64.
    # We have to compute the signature over the bogus body to get past
    # the signature check first; the undecodable test only fires after.
    from adminfoundry.extensions.auth_oauth.state import _sign

    bogus = "!!! not b64 !!!"
    sig = _sign(bogus, _SECRET)
    with pytest.raises(OAuthStateInvalidError, match="undecodable"):
        unseal_state(f"{bogus}.{sig}", _SECRET)


def test_schema_mismatch_rejected():
    """Attacker forges a payload with missing required fields — refused."""
    from adminfoundry.extensions.auth_oauth.state import _sign

    bad_payload = {"state": "x", "code_verifier": "y"}  # missing fields
    body = json.dumps(bad_payload, separators=(",", ":")).encode("utf-8")
    body_b64 = base64.urlsafe_b64encode(body).rstrip(b"=").decode("ascii")
    sig = _sign(body_b64, _SECRET)
    with pytest.raises(OAuthStateInvalidError, match="schema mismatch"):
        unseal_state(f"{body_b64}.{sig}", _SECRET)


def test_expired_cookie_rejected():
    """Cookie older than TTL must be refused — replay-resistant."""
    payload = _make_state(created_at=int(time.time()) - STATE_TTL_SECONDS - 10)
    sealed = seal_state(payload, _SECRET)
    with pytest.raises(OAuthStateExpiredError, match="expired"):
        unseal_state(sealed, _SECRET)


def test_future_dated_cookie_rejected():
    """Forged timestamp pointing into the future — refused (no negative ages)."""
    payload = _make_state(created_at=int(time.time()) + 3600)
    sealed = seal_state(payload, _SECRET)
    with pytest.raises(OAuthStateInvalidError, match="future"):
        unseal_state(sealed, _SECRET)


def test_custom_max_age_overrides_default():
    """Callers can tighten the TTL — e.g. 30s for a particularly sensitive flow."""
    payload = _make_state(created_at=int(time.time()) - 60)
    sealed = seal_state(payload, _SECRET)
    with pytest.raises(OAuthStateExpiredError):
        unseal_state(sealed, _SECRET, max_age_seconds=30)
    # Same cookie passes when we relax the limit.
    out = unseal_state(sealed, _SECRET, max_age_seconds=120)
    assert out.state == payload.state


# --- cookie name selection ---


class _StubURL:
    def __init__(self, scheme: str) -> None:
        self.scheme = scheme


class _StubRequest:
    """Minimal Request stand-in — only ``url.scheme`` is read."""

    def __init__(self, scheme: str) -> None:
        self.url = _StubURL(scheme)


def test_cookie_name_uses_host_prefix_on_https():
    """__Host- prefix is browser-enforced: requires Secure + Path=/ + no Domain.
    We MUST use it on HTTPS to defend against subdomain cookie shadowing."""
    assert cookie_name_for_request(_StubRequest("https")) == COOKIE_NAME_SECURE
    assert COOKIE_NAME_SECURE.startswith("__Host-")


def test_cookie_name_falls_back_on_http():
    """Browsers silently drop __Host- cookies without Secure — dev/HTTP
    needs a plain name."""
    assert cookie_name_for_request(_StubRequest("http")) == COOKIE_NAME_INSECURE
    assert not COOKIE_NAME_INSECURE.startswith("__Host-")


# --- set_state_cookie attributes ---


def test_set_state_cookie_produces_lax_httponly_secure_on_https():
    """The cookie attributes are security-critical — assert them explicitly."""
    from fastapi import Response

    response = Response()
    sealed = seal_state(_make_state(), _SECRET)
    request = _StubRequest("https")

    # Call the real helper.
    from adminfoundry.extensions.auth_oauth.state import set_state_cookie

    set_state_cookie(response, sealed, request=request)

    # Starlette stores set-cookie under "set-cookie" header — there may
    # be only one, so getlist gives us the raw value(s).
    cookies = response.headers.getlist("set-cookie")
    assert len(cookies) == 1
    header = cookies[0]

    assert COOKIE_NAME_SECURE in header
    assert "HttpOnly" in header
    assert "Secure" in header
    assert "samesite=lax" in header.lower()
    assert "Path=/" in header
    assert "Domain=" not in header  # required for the __Host- prefix


def test_set_state_cookie_omits_secure_on_http():
    """Setting Secure on http would mean the browser never sends it back."""
    from fastapi import Response

    from adminfoundry.extensions.auth_oauth.state import set_state_cookie

    response = Response()
    set_state_cookie(response, seal_state(_make_state(), _SECRET), request=_StubRequest("http"))

    header = response.headers.getlist("set-cookie")[0]
    assert "Secure" not in header
    assert COOKIE_NAME_INSECURE in header


def test_clear_state_cookie_emits_expiring_set_cookie():
    """delete_cookie sends a Set-Cookie with Max-Age=0/expired — confirms the
    callback path actually invalidates the cookie."""
    from fastapi import Response

    from adminfoundry.extensions.auth_oauth.state import clear_state_cookie

    response = Response()
    clear_state_cookie(response, request=_StubRequest("https"))

    header = response.headers.getlist("set-cookie")[0]
    assert COOKIE_NAME_SECURE in header
    # Starlette uses an expired Expires date AND Max-Age=0 to delete.
    assert "Max-Age=0" in header or "Expires=" in header
