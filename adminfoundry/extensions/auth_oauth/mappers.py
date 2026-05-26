"""Concrete :class:`OIDCClaimMapper` implementations.

Ships the Google mapper because Google's OIDC claim shape is the most
commonly-asked use case. Adding GitHub / Microsoft / Authentik later
is the same pattern: a small class that pulls the IdP-specific field
names into the neutral :class:`ExternalIdentityData` slots.

What stays OUT of the mappers (in :mod:`verifier` or never):

* Token signature verification — that's the verifier's job, against
  the JWKS document the :class:`JWKSClient` fetches.
* User provisioning (find-or-create the local user) — that's the
  :class:`OAuthCapableUserProvider`'s job, called by the router after
  the mapper runs.
* Raw token storage — the mapper sees claims, not tokens. Tokens are
  discarded after the ID-token is verified (login-only flow, no
  refresh_token retention).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from adminfoundry.extensions.auth_oauth.base import (
    InvalidClaimsError,
    OIDCClaimMapper,
)
from adminfoundry.extensions.auth_oauth.dto import ExternalIdentityData

#: Claims we DO map to typed fields. Everything else falls into
#: ``raw_extra`` (transient — never persisted, never logged).
_GOOGLE_TYPED_CLAIMS: frozenset[str] = frozenset(
    {
        "sub",
        "email",
        "email_verified",
        "name",
        "given_name",
        "family_name",
        "picture",
        "locale",
        "hd",
    }
)


class GoogleOIDCClaimMapper(OIDCClaimMapper):
    """Maps Google's OIDC ID-token claims to :class:`ExternalIdentityData`.

    Claim → field mapping::

        sub             → provider_subject     (REQUIRED)
        email           → email_at_provider
        email_verified  → email_verified
        name            → name
        given_name      → given_name
        family_name     → family_name
        picture         → picture_url
        locale          → locale
        hd              → hosted_domain       (Workspace domain)

    Anything else lands in ``raw_extra`` for transient consumption.
    """

    provider: str = "google"

    def __call__(self, claims: Mapping[str, Any]) -> ExternalIdentityData:
        sub = claims.get("sub")
        if not sub or not isinstance(sub, str):
            raise InvalidClaimsError(
                "Google OIDC claims are missing the required 'sub' field"
            )

        email_verified_raw = claims.get("email_verified")
        # Google sends booleans, but be defensive — string "true" surfaces
        # in some test fixtures.
        if isinstance(email_verified_raw, str):
            email_verified: bool | None = email_verified_raw.lower() == "true"
        elif isinstance(email_verified_raw, bool):
            email_verified = email_verified_raw
        else:
            email_verified = None

        extras = {k: v for k, v in claims.items() if k not in _GOOGLE_TYPED_CLAIMS}

        return ExternalIdentityData(
            provider=self.provider,
            provider_subject=sub,
            email_at_provider=claims.get("email"),
            email_verified=email_verified,
            name=claims.get("name"),
            given_name=claims.get("given_name"),
            family_name=claims.get("family_name"),
            picture_url=claims.get("picture"),
            locale=claims.get("locale"),
            hosted_domain=claims.get("hd"),
            raw_extra=extras,
        )
