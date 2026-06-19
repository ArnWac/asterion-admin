"""Tests for the OIDC ID-token verifier — Phase 8b.5.

Every check the verifier performs corresponds to an attack the
ID-token flow has to survive. The tests are organised by the attack
each one defends against:

* signature stripping (``alg: none``)
* algorithm confusion (HS256 swap)
* signing-key rotation (kid not in JWKS)
* signature tampering (body modified, sig kept)
* token replay across providers (wrong issuer)
* token replay across apps (wrong audience)
* multi-audience confusion (azp missing/mismatched)
* expiry replay (token from a stale session)
* clock-skew sensitivity (a few seconds of drift must still work)
* nonce replay (token from a previous login attempt)
* missing nonce (degraded IdP behaviour)
* malformed JWT (garbage in the cookie)

Test fixtures generate a fresh RSA keypair per test module — the keys
never leave memory, so there's no risk of leaking them.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk as jose_jwk
from jose import jwt as jose_jwt

from asterion.extensions.auth_oauth.jwks import (
    JWKSClient,
    JWKSKeyNotFoundError,
)
from asterion.extensions.auth_oauth.verifier import (
    IDTokenAudienceError,
    IDTokenExpiredError,
    IDTokenIssuerError,
    IDTokenMalformedError,
    IDTokenNonceError,
    IDTokenSignatureError,
    verify_id_token,
)

_ISSUER = "https://idp.example.com"
_AUDIENCE = "client-123"
_NONCE = "nonce-xyz"
_KID = "test-kid-1"


# ---- fixtures: keypair + JWK + signing helper ----


def _generate_keypair() -> tuple[str, dict]:
    """Return (private PEM, public JWK dict). Fresh per test for isolation."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    public_jwk = jose_jwk.construct(public_pem, algorithm="RS256").to_dict()
    # jose.jwk.construct strips down to the wire-format fields; restore
    # the metadata the JWKS document carries.
    public_jwk["kid"] = _KID
    public_jwk["alg"] = "RS256"
    public_jwk["use"] = "sig"
    return private_pem, public_jwk


def _mint_token(
    private_pem: str,
    *,
    claims: dict | None = None,
    headers: dict | None = None,
    algorithm: str = "RS256",
) -> str:
    """Mint an ID-token signed by ``private_pem``. Defaults to a
    minimally-valid token signed correctly for the configured kid."""
    now = int(time.time())
    base_claims = {
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "sub": "user-abc",
        "exp": now + 600,
        "iat": now,
        "nonce": _NONCE,
        "email": "alice@example.com",
        "email_verified": True,
    }
    if claims:
        base_claims.update(claims)
    base_headers = {"kid": _KID}
    if headers:
        base_headers.update(headers)
    return jose_jwt.encode(
        base_claims,
        private_pem,
        algorithm=algorithm,
        headers=base_headers,
    )


class _StubJWKSClient:
    """Test double — returns a fixed key map, no HTTP."""

    def __init__(self, keys: dict[str, dict]) -> None:
        self._keys = keys

    async def get_key(self, kid: str) -> dict:
        if kid not in self._keys:
            raise JWKSKeyNotFoundError(f"kid={kid!r} not present")
        return self._keys[kid]


def _client_with_key(public_jwk: dict) -> _StubJWKSClient:
    return _StubJWKSClient({public_jwk["kid"]: public_jwk})


def _run(coro):
    return asyncio.run(coro)


# ---- happy path ----


def test_verifies_well_formed_token_and_returns_claims():
    priv, pub = _generate_keypair()
    token = _mint_token(priv)
    claims = _run(
        verify_id_token(
            token,
            jwks_client=_client_with_key(pub),
            issuer=_ISSUER,
            audience=_AUDIENCE,
            nonce=_NONCE,
        )
    )
    assert claims["sub"] == "user-abc"
    assert claims["email"] == "alice@example.com"
    assert claims["nonce"] == _NONCE


def test_multi_audience_with_correct_azp_passes():
    """When aud is a list, azp must equal our client_id — and does."""
    priv, pub = _generate_keypair()
    token = _mint_token(
        priv,
        claims={"aud": [_AUDIENCE, "other-app"], "azp": _AUDIENCE},
    )
    claims = _run(
        verify_id_token(
            token,
            jwks_client=_client_with_key(pub),
            issuer=_ISSUER,
            audience=_AUDIENCE,
            nonce=_NONCE,
        )
    )
    assert claims["azp"] == _AUDIENCE


# ---- signature / algorithm attacks ----


def test_alg_none_rejected():
    """A token with ``alg: none`` carries no signature — must be refused
    even if the rest of the claims look fine."""
    priv, pub = _generate_keypair()
    # We can't actually mint an alg:none token via jose (it refuses).
    # Manually craft the JWT instead.
    import base64
    import json

    header = (
        base64.urlsafe_b64encode(json.dumps({"alg": "none", "kid": _KID}).encode())
        .rstrip(b"=")
        .decode()
    )
    payload = (
        base64.urlsafe_b64encode(
            json.dumps({"iss": _ISSUER, "aud": _AUDIENCE, "sub": "x", "nonce": _NONCE}).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    forged = f"{header}.{payload}."

    with pytest.raises(IDTokenSignatureError, match="not allowed"):
        _run(
            verify_id_token(
                forged,
                jwks_client=_client_with_key(pub),
                issuer=_ISSUER,
                audience=_AUDIENCE,
                nonce=_NONCE,
            )
        )


def test_hs256_algorithm_rejected_even_with_valid_hmac():
    """Algorithm confusion attack: attacker signs an HS256 token using
    the RSA public key as the symmetric secret. We refuse all non-RS256
    algorithms upfront, before any signature check."""
    priv, pub = _generate_keypair()
    # Mint an HS256 token with the public JWK as the 'secret'. The
    # signature is technically correct under HS256, but we don't allow
    # HS256 at all, so this never reaches the sig check.
    pub_pem_n = pub["n"]  # any string suffices — we never get to verify
    forged = jose_jwt.encode(
        {
            "iss": _ISSUER,
            "aud": _AUDIENCE,
            "sub": "x",
            "nonce": _NONCE,
            "exp": int(time.time()) + 600,
            "iat": int(time.time()),
        },
        pub_pem_n,
        algorithm="HS256",
        headers={"kid": _KID},
    )
    with pytest.raises(IDTokenSignatureError, match="not allowed"):
        _run(
            verify_id_token(
                forged,
                jwks_client=_client_with_key(pub),
                issuer=_ISSUER,
                audience=_AUDIENCE,
                nonce=_NONCE,
            )
        )


def test_kid_not_in_jwks_rejected():
    """The token's kid doesn't match any cached key, and the JWKS refresh
    doesn't find it either."""
    priv, _ = _generate_keypair()
    _, other_pub = _generate_keypair()  # a DIFFERENT key in the JWKS
    other_pub["kid"] = "different-kid"
    token = _mint_token(priv)  # signed by the original; kid=test-kid-1
    with pytest.raises(IDTokenSignatureError, match="resolve signing key"):
        _run(
            verify_id_token(
                token,
                jwks_client=_StubJWKSClient({"different-kid": other_pub}),
                issuer=_ISSUER,
                audience=_AUDIENCE,
                nonce=_NONCE,
            )
        )


def test_missing_kid_in_header_rejected():
    """Some IdPs technically allow a single-key JWKS without kid, but our
    verifier is strict — we need kid to pick the right key safely."""
    priv, pub = _generate_keypair()
    # We have to override the headers to remove kid. jose adds it back
    # if it's None, so pass an empty mapping then drop kid manually via
    # a different signing path.
    import base64
    import json

    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "iss": _ISSUER,
                    "aud": _AUDIENCE,
                    "sub": "x",
                    "nonce": _NONCE,
                    "exp": int(time.time()) + 600,
                    "iat": int(time.time()),
                }
            ).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    # Sign manually with python-jose's low-level interface.
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from jose.utils import base64url_encode

    signing_input = f"{header}.{payload}".encode()
    key_obj = load_pem_private_key(priv.encode(), password=None)
    signature = key_obj.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    sig_b64 = base64url_encode(signature).decode().rstrip("=")
    token = f"{header}.{payload}.{sig_b64}"

    with pytest.raises(IDTokenSignatureError, match="missing 'kid'"):
        _run(
            verify_id_token(
                token,
                jwks_client=_client_with_key(pub),
                issuer=_ISSUER,
                audience=_AUDIENCE,
                nonce=_NONCE,
            )
        )


def test_tampered_signature_rejected():
    """Flip a char in the middle of the signature — verification must fail.

    (Tampering only the LAST char can be a no-op: b64url chars carry
    6 bits, but the RSA signature is a fixed 256 bytes, so the trailing
    char's low bits don't map to signature bytes and a flip there may
    decode identically.)"""
    priv, pub = _generate_keypair()
    token = _mint_token(priv)
    head, payload, sig = token.split(".")
    mid = len(sig) // 2
    swapped = sig[:mid] + ("A" if sig[mid] != "A" else "B") + sig[mid + 1 :]
    tampered = f"{head}.{payload}.{swapped}"
    with pytest.raises(IDTokenSignatureError):
        _run(
            verify_id_token(
                tampered,
                jwks_client=_client_with_key(pub),
                issuer=_ISSUER,
                audience=_AUDIENCE,
                nonce=_NONCE,
            )
        )


# ---- claim attacks ----


def test_wrong_issuer_rejected():
    priv, pub = _generate_keypair()
    token = _mint_token(priv, claims={"iss": "https://attacker.example.com"})
    with pytest.raises(IDTokenIssuerError):
        _run(
            verify_id_token(
                token,
                jwks_client=_client_with_key(pub),
                issuer=_ISSUER,
                audience=_AUDIENCE,
                nonce=_NONCE,
            )
        )


def test_wrong_audience_rejected():
    priv, pub = _generate_keypair()
    token = _mint_token(priv, claims={"aud": "different-client"})
    with pytest.raises(IDTokenAudienceError):
        _run(
            verify_id_token(
                token,
                jwks_client=_client_with_key(pub),
                issuer=_ISSUER,
                audience=_AUDIENCE,
                nonce=_NONCE,
            )
        )


def test_multi_audience_without_azp_rejected():
    priv, pub = _generate_keypair()
    # aud is a list; azp is missing entirely.
    token = _mint_token(priv, claims={"aud": [_AUDIENCE, "other-app"]})
    with pytest.raises(IDTokenAudienceError, match="azp"):
        _run(
            verify_id_token(
                token,
                jwks_client=_client_with_key(pub),
                issuer=_ISSUER,
                audience=_AUDIENCE,
                nonce=_NONCE,
            )
        )


def test_multi_audience_with_wrong_azp_rejected():
    priv, pub = _generate_keypair()
    token = _mint_token(
        priv,
        claims={"aud": [_AUDIENCE, "other-app"], "azp": "wrong-app"},
    )
    with pytest.raises(IDTokenAudienceError, match="azp"):
        _run(
            verify_id_token(
                token,
                jwks_client=_client_with_key(pub),
                issuer=_ISSUER,
                audience=_AUDIENCE,
                nonce=_NONCE,
            )
        )


# ---- time-based attacks ----


def test_expired_token_rejected():
    priv, pub = _generate_keypair()
    now = int(time.time())
    token = _mint_token(priv, claims={"exp": now - 3600, "iat": now - 7200})
    with pytest.raises(IDTokenExpiredError):
        _run(
            verify_id_token(
                token,
                jwks_client=_client_with_key(pub),
                issuer=_ISSUER,
                audience=_AUDIENCE,
                nonce=_NONCE,
            )
        )


def test_small_clock_skew_within_leeway_accepted():
    """A token issued 30s in the future should still verify (60s leeway)."""
    priv, pub = _generate_keypair()
    now = int(time.time())
    token = _mint_token(priv, claims={"iat": now + 30, "exp": now + 630})
    claims = _run(
        verify_id_token(
            token,
            jwks_client=_client_with_key(pub),
            issuer=_ISSUER,
            audience=_AUDIENCE,
            nonce=_NONCE,
        )
    )
    assert claims["sub"] == "user-abc"


# ---- nonce ----


def test_wrong_nonce_rejected():
    priv, pub = _generate_keypair()
    token = _mint_token(priv, claims={"nonce": "replayed-nonce"})
    with pytest.raises(IDTokenNonceError):
        _run(
            verify_id_token(
                token,
                jwks_client=_client_with_key(pub),
                issuer=_ISSUER,
                audience=_AUDIENCE,
                nonce=_NONCE,
            )
        )


def test_missing_nonce_rejected():
    """Some IdPs don't echo the nonce — refuse rather than degrade silently."""
    priv, pub = _generate_keypair()
    token = _mint_token(priv, claims={"nonce": None})
    # jose serializes None as null; remove the key entirely instead.
    # Easier: re-encode with a claim set that drops nonce.
    now = int(time.time())
    token = jose_jwt.encode(
        {"iss": _ISSUER, "aud": _AUDIENCE, "sub": "x", "exp": now + 600, "iat": now},  # no nonce!
        priv,
        algorithm="RS256",
        headers={"kid": _KID},
    )
    with pytest.raises(IDTokenNonceError):
        _run(
            verify_id_token(
                token,
                jwks_client=_client_with_key(pub),
                issuer=_ISSUER,
                audience=_AUDIENCE,
                nonce=_NONCE,
            )
        )


# ---- malformed ----


def test_garbage_string_rejected():
    _, pub = _generate_keypair()
    with pytest.raises(IDTokenMalformedError):
        _run(
            verify_id_token(
                "not.even.a-jwt",
                jwks_client=_client_with_key(pub),
                issuer=_ISSUER,
                audience=_AUDIENCE,
                nonce=_NONCE,
            )
        )


def test_empty_string_rejected():
    _, pub = _generate_keypair()
    with pytest.raises(IDTokenMalformedError):
        _run(
            verify_id_token(
                "",
                jwks_client=_client_with_key(pub),
                issuer=_ISSUER,
                audience=_AUDIENCE,
                nonce=_NONCE,
            )
        )


# ---- integration: real JWKSClient ----


def test_uses_real_jwks_client_to_resolve_key():
    """Smoke test that verify_id_token works against the real JWKSClient
    (not just the stub), via a mocked httpx transport."""
    import httpx

    priv, pub = _generate_keypair()
    token = _mint_token(priv)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"keys": [pub]})

    async def _go():
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = JWKSClient(
            "https://idp.example.com/.well-known/jwks.json",
            http_client=http,
        )
        try:
            return await verify_id_token(
                token,
                jwks_client=client,
                issuer=_ISSUER,
                audience=_AUDIENCE,
                nonce=_NONCE,
            )
        finally:
            await client.aclose()

    claims = _run(_go())
    assert claims["sub"] == "user-abc"
