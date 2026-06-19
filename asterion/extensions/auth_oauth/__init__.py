"""OAuth/OIDC extension — full redirect flow.

The first extension that hangs off the Phase-5 ``AdminExtension`` SPI
and uses the Phase-8b.1 ``register_models`` hook. Validates the
architecture end-to-end: an external auth backend the framework knows
nothing about plugs in via ``extensions=[…]`` and contributes
permissions, protected fields, contract fragments, a persisted DB
table, AND mounted routes that complete a real OIDC redirect flow.

**What's HERE:**

* :class:`OAuthProvider` / :class:`OIDCClaimMapper` Protocols
  (``base.py``).
* :class:`ExternalIdentityData` DTO and :class:`OAuthProviderConfig`
  config wrapper (``dto.py``).
* :class:`GoogleOIDCClaimMapper` — pure claim-mapping function
  (``mappers.py``).
* :class:`GoogleOIDCProvider` adapter bundle with hard-coded Google
  endpoints + ``build_authorize_url`` / ``exchange_code`` (``providers.py``).
* :class:`ExternalIdentity` persistent model with ``(provider,
  provider_subject)`` unique constraint (``models.py``).
* Sealed-cookie state + PKCE storage (``state.py``).
* Cached, request-coalescing :class:`JWKSClient` (``jwks.py``).
* End-to-end ID-token verifier with strict alg/iss/aud/exp/nonce
  checks (``verifier.py``).
* :class:`OAuthCapableUserProvider` Protocol + :class:`BuiltinOAuthUserProvider`
  default impl with safe lookup-only defaults (``user_provider.py``).
* Real ``/login`` + ``/callback`` handlers that tie it all together
  and end in a fragment-redirect with a framework JWT (``router.py``).
* :class:`OAuthExtension` — AdminExtension subclass that wires all of
  the above and manages the per-process httpx + JWKS clients via the
  Phase-5 ``startup`` / ``shutdown`` hooks.

**Migration story:** the framework ships NO Alembic migration for
``external_identities``. Host apps wiring this extension run
``alembic --autogenerate`` against their own env.py — see
``models.py`` and ``docs/extensions.md`` for the rationale.

**What we deliberately don't ship:**

* Token refresh / API access to Google services (Drive/Gmail). Login
  only — the access_token is discarded after the ID-token verifies.
* Persistent token storage. No table holds ``access_token`` /
  ``refresh_token`` / ``id_token``; an extension that needs offline
  IdP API access would add its own model + flow.
* SAML / SCIM. Different protocols, different extension.

Usage::

    from asterion import create_admin
    from asterion.extensions.auth_oauth import (
        OAuthExtension,
        GoogleOIDCProvider,
    )

    app = create_admin(
        config=...,
        extensions=[
            OAuthExtension(
                providers=[
                    GoogleOIDCProvider(client_id="...", client_secret="..."),
                ],
                # Default is lookup-only — pre-provisioned users only.
                # Set True to auto-create users for verified emails;
                # see BuiltinOAuthUserProvider for the security defaults.
                auto_create_users=False,
            ),
        ],
    )

``GET /api/v1/admin/_contract`` exposes the configured providers in
``extensions.auth_oauth.providers``; ``GET /api/v1/oauth/{id}/login``
starts the redirect flow.
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from asterion.extensions.auth_oauth.base import (
    InvalidClaimsError,
    OAuthProvider,
    OIDCClaimMapper,
)
from asterion.extensions.auth_oauth.dto import (
    ExternalIdentityData,
    OAuthProviderConfig,
)
from asterion.extensions.auth_oauth.jwks import JWKSClient
from asterion.extensions.auth_oauth.mappers import GoogleOIDCClaimMapper
from asterion.extensions.auth_oauth.models import ExternalIdentity
from asterion.extensions.auth_oauth.providers import GoogleOIDCProvider
from asterion.extensions.auth_oauth.router import build_oauth_router
from asterion.extensions.auth_oauth.user_provider import (
    BuiltinOAuthUserProvider,
    OAuthAutoCreateDisabledError,
    OAuthCapabilityError,
    OAuthCapableUserProvider,
    OAuthEmailCollisionError,
    OAuthEmailNotVerifiedError,
    OAuthUserInactiveError,
)
from asterion.extensions.base import AdminExtension
from asterion.extensions.context import ExtensionContext

#: Permission keys this extension claims as its own. A future
#: ``ExternalIdentity`` admin would gate on ``oauth.identities.list``;
#: an "unlink my Google account" UI button would require
#: ``oauth.identities.unlink``. Tenant roles can grant either.
_PERMISSION_KEYS: tuple[str, ...] = (
    "oauth.identities.list",
    "oauth.identities.unlink",
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
    """The OAuth/OIDC extension — multi-provider redirect-flow integration."""

    name = "auth_oauth"

    def __init__(
        self,
        *,
        providers: list[OAuthProvider] | None = None,
        user_provider: OAuthCapableUserProvider | None = None,
        auto_create_users: bool = False,
    ) -> None:
        self._providers: list[OAuthProvider] = list(providers or [])
        seen_ids: set[str] = set()
        for prov in self._providers:
            if prov.config.id in seen_ids:
                raise ValueError(f"OAuthExtension: duplicate provider id {prov.config.id!r}")
            seen_ids.add(prov.config.id)
        # Cached during ``configure(config)``. Used by
        # ``register_contract_contributions`` (called immediately after)
        # to build absolute login URLs.
        self._cached_auth_prefix: str = "/api/v1/auth"
        # Identity write path. Defaults to the framework's built-in
        # User model; apps with their own identity system pass a
        # custom OAuthCapableUserProvider here. Lookup-only by default
        # — auto-create requires an explicit opt-in by the operator
        # (see the dataclass / Phase 8b.6 security defaults).
        self._user_provider: OAuthCapableUserProvider = user_provider or BuiltinOAuthUserProvider()
        self._auto_create_users: bool = auto_create_users
        # Async resources created in startup, torn down in shutdown.
        # Typed as Optional because they're None until startup runs;
        # the router asserts non-None before use.
        self._http_client: httpx.AsyncClient | None = None
        self._jwks_clients: dict[str, JWKSClient] = {}

    # ---- Phase 5 lifecycle hooks ----

    def register_permissions(self, registry) -> None:
        registry.register(*_PERMISSION_KEYS)

    def register_models(self):
        # Importing models attaches the ExternalIdentity Table to
        # GlobalBase.metadata at class-definition time. Returning the
        # class lets the runtime track ownership for tooling.
        from asterion.extensions.auth_oauth import models

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

    async def startup(self, app: FastAPI) -> None:
        # One shared httpx client for both the token exchanges and the
        # JWKS clients (each provider gets its own JWKSClient that
        # uses this shared transport). Keeping a single client means
        # we benefit from connection pooling across providers — token
        # exchanges hit accounts.google.com and JWKS hits
        # www.googleapis.com, so the pool helps less in practice than
        # in theory, but it's still simpler than one client per
        # subsystem.
        self._http_client = httpx.AsyncClient(timeout=15.0)
        self._jwks_clients = {}
        for prov in self._providers:
            jwks_uri = getattr(prov, "JWKS_URI", None)
            if not jwks_uri:
                # Skip providers that don't have a JWKS URI declared
                # (purely hypothetical — every concrete OIDC provider
                # we ship has one). The callback handler raises a
                # clean 'internal' error if it can't find a client.
                continue
            self._jwks_clients[prov.config.id] = JWKSClient(
                jwks_uri,
                http_client=self._http_client,
            )

    async def shutdown(self, app: FastAPI) -> None:
        # JWKS clients share the httpx transport — they MUST NOT close
        # it (they were constructed with the http_client kwarg, so
        # aclose() is a no-op for the shared client). We close the
        # shared client exactly once here.
        for jwks in self._jwks_clients.values():
            await jwks.aclose()
        self._jwks_clients = {}
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    @property
    def providers(self) -> tuple[OAuthProvider, ...]:
        return tuple(self._providers)

    @property
    def user_provider(self) -> OAuthCapableUserProvider:
        return self._user_provider

    @property
    def auto_create_users(self) -> bool:
        return self._auto_create_users


__all__ = [
    "BuiltinOAuthUserProvider",
    "ExternalIdentity",
    "ExternalIdentityData",
    "GoogleOIDCClaimMapper",
    "GoogleOIDCProvider",
    "InvalidClaimsError",
    "OAuthAutoCreateDisabledError",
    "OAuthCapabilityError",
    "OAuthCapableUserProvider",
    "OAuthEmailCollisionError",
    "OAuthEmailNotVerifiedError",
    "OAuthExtension",
    "OAuthProvider",
    "OAuthProviderConfig",
    "OAuthUserInactiveError",
    "OIDCClaimMapper",
]
