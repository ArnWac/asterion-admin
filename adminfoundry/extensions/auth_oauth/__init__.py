"""OAuth/OIDC extension — Phase 8a skeleton.

The first extension that hangs off the Phase-5 ``AdminExtension`` SPI to
prove the architecture really lets external auth backends in without
core changes. It validates that the four extension-side registries
(permissions / protected fields / contract contributions / navigation)
are sufficient to describe "an OAuth provider that doesn't exist in the
framework".

**Skeleton scope — what is HERE in v1:**

* :class:`OAuthProvider` / :class:`OIDCClaimMapper` Protocols
  (``base.py``).
* :class:`ExternalIdentityData` DTO and :class:`OAuthProviderConfig`
  config wrapper (``dto.py``).
* :class:`GoogleOIDCClaimMapper` — pure claim-mapping function
  (``mappers.py``).
* :class:`GoogleOIDCProvider` adapter bundle (``providers.py``).
* :class:`OAuthExtension` — Phase-5 ``AdminExtension`` subclass that
  registers permissions, protected fields, contract contributions, and
  placeholder routes.

**What lands in Phase 8b (NOT in this skeleton):**

* Actual OAuth redirect flow: state + PKCE storage (Cookie + verifier
  hash), authorize URL construction, code-for-token exchange.
* JWKS client with key rotation + cache for ID-token verification.
* Find-or-create user via the framework's ``UserProvider``.
* JWT minting after successful login + fragment-redirect to the UI
  ``/admin/login-complete#token=…``.
* Persisted ``ExternalIdentity`` model with ``(provider,
  provider_subject)`` unique constraint + Alembic migration. (Needs
  the framework SPI for extension DB models — a separate Phase 8a-prereq
  decision.)

**What we do NOT plan to ship even in Phase 8b:**

* Token refresh / API access to Google services (Drive/Gmail). Login
  only.
* OAuth credential storage (``OAuthCredential`` table). Add only when
  an extension actually needs offline access tokens.
* SAML / SCIM. Different protocols, different extension.

Usage::

    from adminfoundry import create_admin
    from adminfoundry.extensions.auth_oauth import (
        OAuthExtension,
        GoogleOIDCProvider,
    )

    app = create_admin(
        config=...,
        extensions=[
            OAuthExtension(providers=[
                GoogleOIDCProvider(client_id="...", client_secret="..."),
            ]),
        ],
    )

Hitting ``GET /api/v1/admin/_contract`` shows an ``extensions.auth_oauth``
fragment listing the configured providers. Hitting
``GET /api/v1/oauth/google/login`` returns 501 with a "Phase 8b not
implemented" body for the duration of v1.
"""

from __future__ import annotations

from fastapi import FastAPI

from adminfoundry.extensions.auth_oauth.base import (
    InvalidClaimsError,
    OAuthProvider,
    OIDCClaimMapper,
)
from adminfoundry.extensions.auth_oauth.dto import (
    ExternalIdentityData,
    OAuthProviderConfig,
)
from adminfoundry.extensions.auth_oauth.mappers import GoogleOIDCClaimMapper
from adminfoundry.extensions.auth_oauth.models import ExternalIdentity
from adminfoundry.extensions.auth_oauth.providers import GoogleOIDCProvider
from adminfoundry.extensions.auth_oauth.router import build_oauth_router
from adminfoundry.extensions.base import AdminExtension
from adminfoundry.extensions.context import ExtensionContext

#: Permission keys this extension claims as its own. Phase 8b's
#: ``ExternalIdentity`` admin would gate on ``oauth.identities.list``;
#: an "unlink my Google account" UI button would require
#: ``oauth.identities.unlink``. Tenant roles can grant either.
_PERMISSION_KEYS: tuple[str, ...] = (
    "oauth.identities.list",
    "oauth.identities.unlink",
)

#: Fields the extension's future ``ExternalIdentity`` / ``OAuthCredential``
#: models would expose if they were persisted today. Registering them
#: NOW means the protected-field registry already knows about them when
#: those models land — no risk of a token slipping through a serializer
#: written before the registration.
_PROTECTED_FIELDS: tuple[str, ...] = (
    "access_token",
    "refresh_token",
    "id_token",
    "client_secret",
)


def _api_base_from_auth_prefix(auth_api_prefix: str) -> str:
    """Strip the trailing ``/auth`` segment from ``auth_api_prefix`` to
    get the shared API base where ``/oauth`` lands too.

    Default ``/api/v1/auth`` → ``/api/v1``. Apps with a custom auth
    prefix that doesn't end in ``/auth`` get the prefix back unchanged —
    they're then responsible for accepting that ``/oauth`` mounts as a
    sibling of whatever they configured.
    """
    if auth_api_prefix.endswith("/auth"):
        return auth_api_prefix[: -len("/auth")] or "/"
    return auth_api_prefix


class OAuthExtension(AdminExtension):
    """The OAuth/OIDC extension — multi-provider, skeleton routes."""

    name = "auth_oauth"

    def __init__(self, *, providers: list[OAuthProvider] | None = None) -> None:
        self._providers: list[OAuthProvider] = list(providers or [])
        seen_ids: set[str] = set()
        for prov in self._providers:
            if prov.config.id in seen_ids:
                raise ValueError(
                    f"OAuthExtension: duplicate provider id {prov.config.id!r}"
                )
            seen_ids.add(prov.config.id)
        # Cached during ``configure(config)``. Used by
        # ``register_contract_contributions`` (called immediately after)
        # to build absolute login URLs.
        self._cached_auth_prefix: str = "/api/v1/auth"

    # ---- Phase 5 lifecycle hooks ----

    def register_permissions(self, registry) -> None:
        registry.register(*_PERMISSION_KEYS)

    def register_protected_fields(self, registry) -> None:
        registry.register(*_PROTECTED_FIELDS)

    def register_models(self):
        # Importing models attaches the ExternalIdentity Table to
        # GlobalBase.metadata at class-definition time. Returning the
        # class lets the runtime track ownership for tooling.
        from adminfoundry.extensions.auth_oauth import models

        return (models.ExternalIdentity,)

    def register_contract_contributions(self, registry) -> None:
        # The UI iterates over this fragment to render login buttons.
        # We emit absolute paths here so consumers don't have to know
        # the framework's prefix layout. The path matches what
        # ``register_routes`` mounts below.
        api_base = _api_base_from_auth_prefix(self._cached_auth_prefix)
        fragment = {
            "providers": [
                {
                    "id": p.config.id,
                    "label": p.config.label,
                    "login_url": f"{api_base}/oauth/{p.config.id}/login",
                }
                for p in self._providers
            ]
        }
        registry.add("auth_oauth", fragment)

    def configure(self, config) -> None:
        # Cache the auth prefix so register_contract_contributions can
        # build the login URLs. The contract hook intentionally doesn't
        # receive the full ExtensionContext to keep its signature small.
        self._cached_auth_prefix = config.auth_api_prefix

    def register_routes(self, app: FastAPI, ctx: ExtensionContext) -> None:
        if not self._providers:
            # Nothing to mount; an OAuthExtension with no providers is
            # legal but produces no routes — useful as a "framework
            # opts into the SPI, providers added later" placeholder.
            return
        api_base = _api_base_from_auth_prefix(ctx.config.auth_api_prefix)
        app.include_router(
            build_oauth_router(self._providers),
            prefix=api_base,
        )

    @property
    def providers(self) -> tuple[OAuthProvider, ...]:
        return tuple(self._providers)


__all__ = [
    "ExternalIdentity",
    "ExternalIdentityData",
    "GoogleOIDCClaimMapper",
    "GoogleOIDCProvider",
    "InvalidClaimsError",
    "OAuthExtension",
    "OAuthProvider",
    "OAuthProviderConfig",
    "OIDCClaimMapper",
]
