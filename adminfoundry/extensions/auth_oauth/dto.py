"""DTOs for the OAuth/OIDC extension.

Pure value objects — no SQLAlchemy, no FastAPI, no provider SDK
imports. Two shapes:

* :class:`ExternalIdentityData` — neutral identity carrier the claim
  mapper produces; the router feeds it into
  :class:`OAuthCapableUserProvider.find_or_create_by_external_identity`.
* :class:`OAuthProviderConfig` — public, log-safe configuration the
  framework reads off every provider instance.

The corresponding persisted row lives in
:mod:`adminfoundry.extensions.auth_oauth.models` as
:class:`ExternalIdentity`. This file stays free of ORM imports so
mappers + tests can construct identity data without dragging the
SQLAlchemy stack in.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ExternalIdentityData:
    """Provider-agnostic representation of one external identity.

    Built by an ``OIDCClaimMapper`` from verified token claims and
    handed to the user provider's
    :meth:`find_or_create_by_external_identity`. The persisted
    :class:`~adminfoundry.extensions.auth_oauth.models.ExternalIdentity`
    table materializes a subset of these fields into rows.

    Field naming follows OIDC where possible but stays neutral —
    Google's ``picture`` becomes ``picture_url``, ``hd`` becomes
    ``hosted_domain`` etc. so consumers don't pick up provider-specific
    field names.
    """

    provider: str  #: e.g. "google", "github", "microsoft"
    provider_subject: str  #: the stable IdP-side identifier ("sub" in OIDC)

    email_at_provider: str | None = None
    email_verified: bool | None = None

    name: str | None = None
    given_name: str | None = None
    family_name: str | None = None
    picture_url: str | None = None
    locale: str | None = None
    hosted_domain: str | None = None  #: Google ``hd``; org domain in workspace tenancy

    #: Any claims the mapper kept but doesn't have a typed slot for.
    #: The :class:`ExternalIdentity` row stores only the typed columns
    #: above; ``raw_extra`` is for transient use only (debugging,
    #: custom hooks the host app may run).
    raw_extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OAuthProviderConfig:
    """Public, log-safe configuration for one provider adapter.

    Used in the contract contribution (so the UI knows what login
    buttons to render) and as the URL segment in the route paths
    (``/api/v1/oauth/{id}/login``). Secrets (``client_secret``,
    signing keys) belong on the concrete
    :class:`~adminfoundry.extensions.auth_oauth.base.OAuthProvider`
    instance, not here.
    """

    id: str  #: URL-safe slug, used in route paths: /api/v1/oauth/{id}/login
    label: str  #: human-readable label for the login button ("Google")
