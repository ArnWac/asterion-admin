"""Concrete OAuth/OIDC provider adapters.

Skeleton scope (Phase 8a): each adapter is just a config + claim-mapper
bundle. The redirect-flow methods (``authorize_url``, ``exchange_code``,
``verify_id_token``) land in Phase 8b — implementing them now without
the storage + cookie + JWKS infrastructure to back them would be a lie.

What an app provides today::

    GoogleOIDCProvider(
        id="google",
        label="Google",
        client_id="...",
        client_secret="...",  # never serialized; lives only on this instance
    )

Why ``client_secret`` is on the instance (not a class attribute / env
var): apps with multiple Google client configurations (workspace +
consumer accounts) need to pass them through. Keeping it as an
``__init__`` argument lets the extension hold multiple instances
simultaneously when the real flow lands in Phase 8b.

Adding GitHub / Microsoft / Authentik later is the same pattern: a
small subclass with a matching :class:`OIDCClaimMapper`.
"""

from __future__ import annotations

from adminfoundry.extensions.auth_oauth.base import OAuthProvider, OIDCClaimMapper
from adminfoundry.extensions.auth_oauth.dto import OAuthProviderConfig
from adminfoundry.extensions.auth_oauth.mappers import GoogleOIDCClaimMapper


class GoogleOIDCProvider(OAuthProvider):
    """OIDC provider adapter for Google Identity.

    The framework only sees :attr:`config` and :attr:`claim_mapper`. The
    ``client_id`` / ``client_secret`` are stored on the instance and
    will be consumed by the redirect-flow methods in Phase 8b.
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        id: str = "google",
        label: str = "Google",
    ) -> None:
        if not client_id or not client_secret:
            raise ValueError(
                "GoogleOIDCProvider requires non-empty client_id and client_secret"
            )
        self.config = OAuthProviderConfig(id=id, label=label)
        self.claim_mapper: OIDCClaimMapper = GoogleOIDCClaimMapper()
        # Stored privately — never serialized, never logged, never
        # surfaced to the framework's protected-field-stripping layers
        # (because they're not on a model in the first place).
        self._client_id = client_id
        self._client_secret = client_secret

    def __repr__(self) -> str:
        # Don't include client_secret. ``client_id`` is generally public,
        # but we redact it too to keep the repr safely loggable.
        return f"<GoogleOIDCProvider id={self.config.id!r}>"
