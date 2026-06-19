"""OIDC ID-token verifier — Phase 8b.5.

Validates an ID-token end-to-end against an IdP's JWKS, the
provider-specific issuer/audience, and the per-request nonce the
sealed-cookie state carried through the redirect. The function returns
the verified claim set; callers feed it into their claim-mapper to
build an :class:`ExternalIdentityData`.

The checks, in order:

1. Parse the JWT header — refuse anything we can't decode.
2. Reject ``alg: none`` or unexpected algorithms via the explicit
   ``algorithms=["RS256"]`` allowlist. RS256 is the OIDC core spec's
   default and what every major IdP (Google, Microsoft, Auth0,
   Authentik, Keycloak, Okta) ships out of the box.
3. Resolve the signing key via :class:`JWKSClient` using the header's
   ``kid``. Missing kid → refuse (header tampering).
4. Cryptographically verify the signature.
5. Validate ``iss`` matches the configured issuer string.
6. Validate ``aud`` contains the configured client_id; when multiple
   audiences are present, require ``azp`` and check it matches too
   (RFC OIDC §3.1.3.7 step 5).
7. Validate ``exp`` is in the future, ``iat`` is not absurdly in the
   future (clock skew leeway: 60s).
8. Validate the ``nonce`` claim matches the value we put in the sealed
   state cookie before the redirect (replay protection).

Every failure raises a subclass of :class:`IDTokenError` so the
callback handler can branch on the specific failure (logging,
metrics) but the user always sees the same generic "auth failed"
message — we don't leak which check tripped.
"""

from __future__ import annotations

from typing import Any

from jose import jwt
from jose.exceptions import (
    ExpiredSignatureError,
    JWSError,
    JWTClaimsError,
    JWTError,
)

from asterion.extensions.auth_oauth.jwks import JWKSClient, JWKSError

#: We hard-code RS256 because:
#: * every mainstream OIDC IdP advertises it
#: * permitting HS* opens an algorithm-confusion attack where the
#:   attacker swaps a public-key JWS for a symmetric one and signs it
#:   with the public key as the secret
#: * accepting "none" would skip signature checking entirely
_ALLOWED_ALGORITHMS: tuple[str, ...] = ("RS256",)

#: Tolerated clock skew between IdP and verifier. 60s is the de facto
#: industry default (matches Google's libraries, AWS Cognito, etc.).
_LEEWAY_SECONDS: int = 60


class IDTokenError(Exception):
    """Base — catch this to handle any ID-token validation failure."""


class IDTokenMalformedError(IDTokenError):
    """JWT could not be parsed — header decode failure, bad structure."""


class IDTokenSignatureError(IDTokenError):
    """Signature check failed or signing algorithm not allowed."""


class IDTokenExpiredError(IDTokenError):
    """``exp`` in the past, or ``iat`` too far in the future."""


class IDTokenIssuerError(IDTokenError):
    """``iss`` claim does not match the configured issuer."""


class IDTokenAudienceError(IDTokenError):
    """``aud``/``azp`` claim does not match the configured client_id."""


class IDTokenNonceError(IDTokenError):
    """``nonce`` claim missing or different from the expected value."""


async def verify_id_token(
    id_token: str,
    *,
    jwks_client: JWKSClient,
    issuer: str,
    audience: str,
    nonce: str,
    leeway_seconds: int = _LEEWAY_SECONDS,
) -> dict[str, Any]:
    """Validate an OIDC ID-token end-to-end.

    Returns the verified claims dict. Raises an :class:`IDTokenError`
    subclass on any failure.

    The function is async because it may need to fetch / refresh the
    JWKS document. Cache hits (the common case) don't await on I/O.
    """
    # --- 1. Parse the header so we can resolve the signing key ---
    try:
        header = jwt.get_unverified_header(id_token)
    except (JWTError, ValueError, TypeError) as exc:
        # ValueError/TypeError because get_unverified_header has been
        # known to choke on non-string input before raising JWTError.
        raise IDTokenMalformedError("could not parse ID-token header") from exc

    alg = header.get("alg")
    if alg not in _ALLOWED_ALGORITHMS:
        # "none" lives here too — refusing all non-allowlisted algorithms
        # closes the algorithm-confusion and unsigned-token attack
        # surfaces in one check.
        raise IDTokenSignatureError(
            f"ID-token algorithm {alg!r} not allowed (expected one of {list(_ALLOWED_ALGORITHMS)})"
        )

    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        # Without a kid we can't pick the right signing key from the
        # JWKS. Some IdPs technically permit single-key JWKS without
        # kid, but every major one we care about ships kids — be strict.
        raise IDTokenSignatureError("ID-token header missing 'kid'")

    # --- 2. Resolve the signing key ---
    try:
        jwk = await jwks_client.get_key(kid)
    except JWKSError as exc:
        raise IDTokenSignatureError(
            f"could not resolve signing key for kid={kid!r}: {exc}"
        ) from exc

    # --- 3. Signature + iss + aud + exp + iat checks via python-jose ---
    options = {
        # We control everything — explicit is better than relying on
        # jose's defaults, which have flipped between versions.
        "verify_signature": True,
        "verify_aud": True,
        "verify_iat": True,
        "verify_exp": True,
        "verify_nbf": True,
        "verify_iss": True,
        "require_aud": True,
        "require_exp": True,
        "require_iat": True,
        "require_iss": True,
        # python-jose takes leeway via the options dict, not as a
        # decode() kwarg. Applies to exp / iat / nbf checks.
        "leeway": leeway_seconds,
        # Nonce check is OIDC, not JWT — jose doesn't know about it.
        # We handle it ourselves below after decode succeeds.
    }
    try:
        claims = jwt.decode(
            id_token,
            jwk,
            algorithms=list(_ALLOWED_ALGORITHMS),
            audience=audience,
            issuer=issuer,
            options=options,
        )
    except ExpiredSignatureError as exc:
        raise IDTokenExpiredError("ID-token has expired") from exc
    except JWTClaimsError as exc:
        # jose collapses iss/aud/iat-future failures into one exception
        # type with a free-text message. We unpack the message to give
        # callers a usable distinction.
        message = str(exc).lower()
        if "issuer" in message:
            raise IDTokenIssuerError(str(exc)) from exc
        if "audience" in message:
            raise IDTokenAudienceError(str(exc)) from exc
        # iat-in-future and similar fall through here.
        raise IDTokenExpiredError(str(exc)) from exc
    except JWSError as exc:
        raise IDTokenSignatureError(f"ID-token signature invalid: {exc}") from exc
    except JWTError as exc:
        # python-jose collapses several failure modes into generic
        # JWTError. Inspect the message so callers see a meaningful
        # subclass instead of "everything is malformed".
        message = str(exc).lower()
        if "signature" in message:
            raise IDTokenSignatureError(f"ID-token signature invalid: {exc}") from exc
        raise IDTokenMalformedError(f"ID-token decode failed: {exc}") from exc

    # --- 4. Multi-audience azp check ---
    # OIDC §3.1.3.7 step 5: when the ID-token contains multiple audiences,
    # the `azp` (authorized party) claim MUST be present and equal to our
    # client_id. python-jose's `audience=` check passes as long as our
    # client_id appears anywhere in `aud` — that's necessary but not
    # sufficient when the IdP issued the token to multiple parties.
    aud_claim = claims.get("aud")
    if isinstance(aud_claim, list) and len(aud_claim) > 1:
        azp = claims.get("azp")
        if azp != audience:
            raise IDTokenAudienceError(
                "ID-token has multiple audiences but 'azp' missing or mismatched"
            )

    # --- 5. Nonce check ---
    token_nonce = claims.get("nonce")
    if not isinstance(token_nonce, str) or token_nonce != nonce:
        # Mismatched (or missing) nonce means either the token is being
        # replayed from an earlier flow OR the IdP didn't echo our
        # nonce. Either way, refuse.
        raise IDTokenNonceError("ID-token nonce missing or does not match")

    return claims
