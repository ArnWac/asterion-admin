"""Protocol contracts for OAuth providers + claim mappers.

The OAuth extension is built around two collaborator interfaces:

* :class:`OIDCClaimMapper` — pure function (well, callable) that turns
  IdP-side claims (an opaque ``dict``) into a provider-agnostic
  :class:`~asterion.extensions.auth_oauth.dto.ExternalIdentityData`.
  Provider-specific claim names (Google's ``sub`` / ``hd`` / ``picture``)
  get translated to neutral fields here so the rest of the framework
  never sees provider-specific JSON.
* :class:`OAuthProvider` — bundles a provider's :class:`OIDCClaimMapper`
  with its public configuration (id, label). The concrete subclasses
  (:class:`~asterion.extensions.auth_oauth.GoogleOIDCProvider`)
  add the OAuth/OIDC flow methods on top — :meth:`build_authorize_url`,
  :meth:`exchange_code` — which the router invokes per request.

The Protocol here is intentionally minimal: it only declares the slots
the framework reads (``config``, ``claim_mapper``). The flow methods
live on the concrete subclasses because their shape differs per provider
(form-encoded for Google's token endpoint, JSON for GitHub's, etc.).

Both protocols are ``runtime_checkable`` so ``isinstance(obj, Protocol)``
works in tests and assertions.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from asterion.extensions.auth_oauth.dto import (
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

    Apps constructing providers should subclass
    :class:`~asterion.extensions.auth_oauth.GoogleOIDCProvider`
    (or write their own concrete subclass for another IdP) rather than
    implement this Protocol from scratch — the protocol is the
    type-level spec for what the framework reads off the instance, not
    the recommended building block.
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
