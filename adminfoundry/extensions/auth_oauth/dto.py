"""DTOs for the OAuth/OIDC extension.

Pure value objects — no SQLAlchemy, no FastAPI, no provider SDK
imports. The skeleton ships only what's needed for the claim-mapping
contract test. A future iteration adds:

* ``ExternalIdentity`` (persisted row backed by SQLAlchemy)
* ``OAuthCredential`` (optional, for apps that need API access tokens)

The persisted model is deliberately deferred — see the extension's
module docstring for why DB-model registration for extensions is its
own architectural question.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ExternalIdentityData:
    """Provider-agnostic representation of one external identity.

    Built by an ``OIDCClaimMapper`` from verified token claims. The
    persisted ``ExternalIdentity`` table (Phase 8b) materializes this
    into rows; for the skeleton we only need the DTO so the claim-
    mapping contract is testable in isolation.

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

    #: Any claims the mapper kept but doesn't have a typed slot for. The
    #: ``ExternalIdentity`` model (Phase 8b) MUST NOT persist this dict
    #: as-is — it's for transient use only (debugging, custom hooks).
    raw_extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OAuthProviderConfig:
    """Public, log-safe configuration for one provider adapter.

    Used in the contract contribution (so the UI knows what login
    buttons to render) and in the placeholder route URLs. Secrets
    (``client_secret``, signing keys) belong on the concrete
    :class:`~adminfoundry.extensions.auth_oauth.base.OAuthProvider`
    instance, not here.
    """

    id: str  #: URL-safe slug, used in route paths: /api/v1/oauth/{id}/login
    label: str  #: human-readable label for the login button ("Google")
