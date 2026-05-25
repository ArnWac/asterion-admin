"""Sealed-cookie state + PKCE storage for the OAuth redirect flow.

Phase 8b.3. The OAuth redirect flow has to carry several pieces of
short-lived per-request data through a round-trip via the IdP:

* the OAuth ``state`` parameter (CSRF guard — must round-trip from
  ``/login`` to ``/callback`` unchanged);
* the PKCE ``code_verifier`` (RFC 7636 — only the SHA-256 hash goes to
  the IdP, the verifier stays here);
* the OIDC ``nonce`` (ID-token replay guard);
* the provider id (the callback URL is per-provider, but encoding it
  also in the payload lets us double-check);
* an optional ``return_to`` (relative path the user wanted before
  being bounced to the IdP).

We store all of this in a single HMAC-sealed cookie. No DB, no cleanup
job, no extra moving parts. The cookie is HttpOnly + SameSite=Lax
(required — the IdP redirects back via top-level navigation, which
``Strict`` would drop), Path=/, and Secure when the request scheme is
``https``. On HTTPS we use the ``__Host-`` cookie name prefix, which
forces the browser to reject the cookie if any of those attributes are
missing — defence in depth against a misconfigured server downgrading
the cookie.

TTL: 10 minutes. Long enough for users to clear an IdP MFA challenge,
short enough that a leaked cookie isn't useful for a future flow.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import asdict, dataclass

from fastapi import Request, Response

#: Cookie name used on HTTPS. The ``__Host-`` prefix is a browser-enforced
#: contract: the cookie is dropped unless it ALSO carries Secure, Path=/,
#: and no Domain attribute. Catches misconfigured TLS terminators that
#: would otherwise allow the cookie to leak to subdomains.
COOKIE_NAME_SECURE: str = "__Host-adminfoundry_oauth_state"

#: Fallback cookie name for HTTP (dev / behind a non-TLS proxy). The
#: ``__Host-`` prefix would be silently rejected without ``Secure``, so
#: we use a plain name and accept the weaker guarantees.
COOKIE_NAME_INSECURE: str = "adminfoundry_oauth_state"

#: How long after issue the sealed cookie is accepted. Beyond this the
#: callback handler treats it as expired and refuses to continue.
STATE_TTL_SECONDS: int = 600

#: 32 raw bytes ≈ 43 b64url chars. RFC 6749 §10.12 calls for >= 128 bits
#: of entropy in the state parameter — 256 bits is comfortable headroom.
_STATE_BYTES: int = 32

#: Same shape as state. RFC 7636 §4.1 calls for 32-96 raw bytes in the
#: code_verifier — 64 lands in the middle of the allowed range.
_CODE_VERIFIER_BYTES: int = 64

#: OIDC §15.5.2 nonce — 256 bits matches state.
_NONCE_BYTES: int = 32


class OAuthStateError(Exception):
    """Base class — catch this to handle any state-related failure."""


class OAuthStateInvalidError(OAuthStateError):
    """The cookie was tampered, missing, or malformed."""


class OAuthStateExpiredError(OAuthStateError):
    """The cookie's TTL has elapsed since issue."""


@dataclass(frozen=True, slots=True)
class OAuthFlowState:
    """The payload sealed into the OAuth state cookie.

    All fields are required at /login time. The callback verifies
    ``state`` matches the IdP's echoed value, derives the PKCE challenge
    again from ``code_verifier`` for the token-exchange request, and
    feeds ``nonce`` into the ID-token verifier.
    """

    state: str
    code_verifier: str
    nonce: str
    provider_id: str
    return_to: str
    created_at: int  # unix timestamp seconds — TTL anchor


# ---- generators ----


def generate_state() -> str:
    """RFC 6749 §10.12 OAuth state value — 256 bits of entropy, b64url."""
    return secrets.token_urlsafe(_STATE_BYTES)


def generate_code_verifier() -> str:
    """RFC 7636 §4.1 PKCE verifier — b64url over 64 random bytes."""
    return secrets.token_urlsafe(_CODE_VERIFIER_BYTES)


def generate_nonce() -> str:
    """OIDC §15.5.2 nonce — 256 bits of entropy, b64url."""
    return secrets.token_urlsafe(_NONCE_BYTES)


def code_challenge_from_verifier(verifier: str) -> str:
    """Per RFC 7636 §4.2 — base64url(SHA256(verifier)) without padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# ---- seal / unseal ----


def seal_state(payload: OAuthFlowState, secret_key: str) -> str:
    """Encode + HMAC-SHA256 sign a state payload for cookie transport.

    Format: ``<payload_b64url>.<hmac_b64url>``. The HMAC covers the
    *encoded* payload string (not raw JSON bytes), which means
    verification compares two already-validated UTF-8 strings.
    """
    body = json.dumps(asdict(payload), separators=(",", ":")).encode("utf-8")
    body_b64 = base64.urlsafe_b64encode(body).rstrip(b"=").decode("ascii")
    sig = _sign(body_b64, secret_key)
    return f"{body_b64}.{sig}"


def unseal_state(
    raw_cookie: str,
    secret_key: str,
    *,
    max_age_seconds: int = STATE_TTL_SECONDS,
) -> OAuthFlowState:
    """Decode + verify a sealed state cookie.

    Raises:
        OAuthStateInvalidError: missing cookie, tampered payload, bad
            HMAC, malformed JSON, or schema mismatch.
        OAuthStateExpiredError: TTL exceeded since issue.
    """
    if not raw_cookie or "." not in raw_cookie:
        raise OAuthStateInvalidError("state cookie missing or malformed")

    body_b64, _, sig = raw_cookie.rpartition(".")
    expected = _sign(body_b64, secret_key)
    # Constant-time compare — both args are ascii of fixed length, so
    # ``compare_digest`` is safe and avoids timing leaks under an
    # attacker probing different signatures.
    if not hmac.compare_digest(sig, expected):
        raise OAuthStateInvalidError("state cookie signature mismatch")

    try:
        padded = body_b64 + "=" * (-len(body_b64) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise OAuthStateInvalidError("state cookie payload undecodable") from exc

    try:
        payload = OAuthFlowState(**data)
    except TypeError as exc:
        # Missing field, extra field, wrong type. Treat all as tamper —
        # leaking which field was wrong helps an attacker probe the
        # schema.
        raise OAuthStateInvalidError("state cookie schema mismatch") from exc

    age = int(time.time()) - payload.created_at
    if age < 0:
        # Clock skew or forged timestamp. Refuse rather than guess.
        raise OAuthStateInvalidError("state cookie created in the future")
    if age > max_age_seconds:
        raise OAuthStateExpiredError(
            f"state cookie expired ({age}s > {max_age_seconds}s TTL)"
        )

    return payload


def _sign(value: str, secret_key: str) -> str:
    """HMAC-SHA256 over ``value``, returned as unpadded b64url."""
    mac = hmac.new(
        secret_key.encode("utf-8"),
        value.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")


# ---- cookie helpers ----


def cookie_name_for_request(request: Request) -> str:
    """Pick ``__Host-`` prefixed name on HTTPS, plain name on HTTP.

    The browser silently drops a ``__Host-`` cookie without ``Secure``,
    so HTTP dev servers must use the plain name. We branch on
    ``request.url.scheme`` rather than a config flag because the
    decision needs to track per-request state (TLS-terminating proxies,
    cli-served local dev, etc.).
    """
    return COOKIE_NAME_SECURE if request.url.scheme == "https" else COOKIE_NAME_INSECURE


def set_state_cookie(
    response: Response,
    sealed: str,
    *,
    request: Request,
    max_age_seconds: int = STATE_TTL_SECONDS,
) -> None:
    """Write the sealed state cookie with the right attributes.

    SameSite=Lax (NOT Strict): the IdP's redirect back to /callback is
    a top-level navigation, and Strict would drop the cookie on that
    transition. Lax allows the cookie to be sent on top-level GETs —
    which is exactly what the OAuth callback is.
    """
    secure = request.url.scheme == "https"
    response.set_cookie(
        key=cookie_name_for_request(request),
        value=sealed,
        max_age=max_age_seconds,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_state_cookie(response: Response, *, request: Request) -> None:
    """Drop the state cookie — called from /callback after consumption.

    State cookies are single-use; the callback handler invalidates the
    cookie immediately after reading it so a replay of the callback URL
    finds nothing.
    """
    response.delete_cookie(
        key=cookie_name_for_request(request),
        path="/",
    )
