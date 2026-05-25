"""OAuth/OIDC placeholder routes — Phase 8a skeleton.

Mounts ``GET /api/v1/oauth/{provider_id}/login`` and
``GET /api/v1/oauth/{provider_id}/callback`` for every configured
provider. Both endpoints return 501 with a clear "skeleton — no flow
implemented" message.

Phase 8b will replace these handlers with the real redirect flow
(authorize URL, callback handler, state/PKCE/cookie storage,
ID-token verification via JWKS, find-or-create user, JWT minting,
fragment-redirect to the UI).

Why a placeholder lands in v1 anyway: the contract contribution
already advertises ``login_url: /api/v1/oauth/{id}/login`` to clients.
A UI button that clicks through to a 501 with a useful body is
strictly better than a 404 — operators see exactly which extension
needs to be upgraded.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from adminfoundry.extensions.auth_oauth.base import OAuthProvider

#: Body shape returned by placeholder endpoints.
_PLACEHOLDER_DETAIL: str = (
    "OAuth flow is not yet implemented — this is the Phase 8a skeleton "
    "of OAuthExtension. Phase 8b will replace this endpoint with the "
    "real redirect handler. Track via the v1-providers roadmap."
)


def build_oauth_router(providers: list[OAuthProvider]) -> APIRouter:
    """Build a sub-router that exposes /login + /callback per provider.

    Called from :meth:`OAuthExtension.register_routes` with the
    extension's configured provider list. The provider's
    :attr:`OAuthProviderConfig.id` becomes the URL segment, so
    ``GoogleOIDCProvider`` with ``id="google"`` lands at
    ``/api/v1/oauth/google/login`` once the framework mounts the
    extension under its admin-api prefix.
    """
    router = APIRouter(prefix="/oauth", tags=["auth-oauth"])

    for provider in providers:
        provider_id = provider.config.id
        # Bind ``provider_id`` into the closure via default argument so
        # the loop variable doesn't leak. Each provider gets its own
        # pair of (login, callback) handlers — same body for the skeleton,
        # but Phase 8b will dispatch on the provider's flow methods here.

        @router.get(f"/{provider_id}/login", name=f"oauth_{provider_id}_login")
        async def _login(_provider_id: str = provider_id) -> None:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail=(
                    f"[provider={_provider_id} phase=8a-skeleton] {_PLACEHOLDER_DETAIL}"
                ),
            )

        @router.get(f"/{provider_id}/callback", name=f"oauth_{provider_id}_callback")
        async def _callback(_provider_id: str = provider_id) -> None:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail=(
                    f"[provider={_provider_id} phase=8a-skeleton] {_PLACEHOLDER_DETAIL}"
                ),
            )

    return router
