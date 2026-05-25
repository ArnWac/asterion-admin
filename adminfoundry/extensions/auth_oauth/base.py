"""Protocol contracts for OAuth providers + claim mappers.

The OAuth extension is built around two collaborator interfaces:

* :class:`OIDCClaimMapper` — pure function (well, callable) that turns
  IdP-side claims (an opaque ``dict``) into a provider-agnostic
  :class:`~adminfoundry.extensions.auth_oauth.dto.ExternalIdentityData`.
  This is what the skeleton ships — Google's specific claim names
  (``sub``, ``hd``, ``picture``) get translated to neutral fields, and
  the rest of the framework never sees provider-specific JSON.
* :class:`OAuthProvider` — bundles a provider's :class:`OIDCClaimMapper`
  with its configuration (id, label) and, in the full Phase 8b
  implementation, the OAuth/OIDC flow methods (authorize URL,
  code/token exchange, JWKS verification). The skeleton defines only
  the slots the routes need; the flow methods are added when the
  redirect-flow lands.

Both protocols are ``runtime_checkable`` so ``isinstance(obj, Protocol)``
works in tests and assertions.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from adminfoundry.extensions.auth_oauth.dto import (
    ExternalIdentityData,
    OAuthProviderConfig,
)


@runtime_checkable
class OIDCClaimMapper(Protocol):
    """Maps verified IdP claims to a provider-agnostic identity DTO.

    Implementations MUST validate that the IdP-side stable subject
    identifier is present (OIDC ``sub`` or equivalent) and raise
    :class:`InvalidClaimsError` if it's missing — every persisted
    identity is keyed on ``(provider, provider_subject)`` so a missing
    subject would corrupt the link table.
    """

    provider: str  #: e.g. "google", "github"

    def __call__(self, claims: Mapping[str, Any]) -> ExternalIdentityData: ...


@runtime_checkable
class OAuthProvider(Protocol):
    """One OAuth/OIDC provider adapter wired into ``OAuthExtension``.

    Skeleton surface — the redirect-flow methods (``authorize_url``,
    ``exchange_code``, ``verify_id_token``) land in Phase 8b. Apps
    constructing providers today should subclass
    :class:`~adminfoundry.extensions.auth_oauth.GoogleOIDCProvider`
    rather than implement this Protocol from scratch — the protocol is
    the type-level spec, not the recommended building block.
    """

    #: Public configuration (id + display label). Used by the contract
    #: contribution so the UI can render a login button.
    config: OAuthProviderConfig

    #: Claim mapper for this provider's token format.
    claim_mapper: OIDCClaimMapper


class InvalidClaimsError(ValueError):
    """Raised when claims fail mapper validation.

    Most commonly: missing ``sub`` (or whatever the provider's stable
    subject claim is). The mapper is the only place that knows what
    "valid" means for a given IdP — the framework treats this as a 400
    when surfaced via routes.
    """
