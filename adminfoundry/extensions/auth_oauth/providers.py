"""Concrete OAuth/OIDC provider adapters.

Each adapter bundles the public IdP endpoints (hard-coded — they have
been stable for years and a startup OIDC-discovery fetch buys nothing
useful here) with the OAuth flow methods the router calls in turn:

* :meth:`build_authorize_url` — composes the redirect URL the user's
  browser bounces to.
* :meth:`exchange_code` — POSTs the authorization code + PKCE verifier
  to the IdP's token endpoint and returns the parsed token response.

Adding GitHub / Microsoft / Authentik later is the same pattern: a
small subclass with hard-coded endpoints + a matching
:class:`OIDCClaimMapper`.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx

from adminfoundry.extensions.auth_oauth.base import OAuthProvider, OIDCClaimMapper
from adminfoundry.extensions.auth_oauth.dto import OAuthProviderConfig
from adminfoundry.extensions.auth_oauth.mappers import GoogleOIDCClaimMapper


class TokenExchangeError(Exception):
    """The IdP refused the authorization code → token exchange.

    Network failure, non-2xx response, or a successful response that
    didn't contain the expected ``id_token`` field. The router catches
    this and surfaces a generic "login failed" error to the user.
    """


class GoogleOIDCProvider(OAuthProvider):
    """OIDC provider adapter for Google Identity.

    Endpoints are hard-coded — Google's identity URLs have been stable
    since the OIDC launch in 2014. Discovery (``.well-known/...``)
    would add a startup HTTP fetch for no upside; we keep the static
    constants and re-fetch JWKS lazily via the cached client.
    """

    #: Google's OAuth 2.0 authorize endpoint (v2 — supports PKCE).
    AUTHORIZE_ENDPOINT: str = "https://accounts.google.com/o/oauth2/v2/auth"

    #: Google's token exchange endpoint.
    TOKEN_ENDPOINT: str = "https://oauth2.googleapis.com/token"

    #: Google's signing-key JWKS endpoint. Feeds :class:`JWKSClient`.
    JWKS_URI: str = "https://www.googleapis.com/oauth2/v3/certs"

    #: Expected ``iss`` claim. The verifier compares the ID token's
    #: ``iss`` against this exactly. Google sometimes ships
    #: ``https://accounts.google.com`` AND ``accounts.google.com`` (no
    #: scheme) — both are documented as valid, but the spec-compliant
    #: form is the URL.
    ISSUER: str = "https://accounts.google.com"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        id: str = "google",
        label: str = "Google",
        scopes: list[str] | None = None,
        extra_authorize_params: dict[str, str] | None = None,
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
        #: OIDC scopes — ``openid`` is REQUIRED to receive an ID token;
        #: ``email`` and ``profile`` populate the claims the mapper
        #: consumes.
        self._scopes: tuple[str, ...] = tuple(
            scopes or ("openid", "email", "profile")
        )
        #: Pass-through bag for advanced flags Google supports —
        #: ``hd=acme.example`` to restrict to a Workspace domain,
        #: ``prompt=consent`` to force the consent screen, etc.
        self._extra_authorize_params: dict[str, str] = dict(
            extra_authorize_params or {}
        )

    def __repr__(self) -> str:
        # Don't include client_secret. ``client_id`` is generally public,
        # but we redact it too to keep the repr safely loggable.
        return f"<GoogleOIDCProvider id={self.config.id!r}>"

    @property
    def client_id(self) -> str:
        """Used as the OIDC ``audience`` claim during ID-token verification."""
        return self._client_id

    # ---- flow methods (Phase 8b) ----

    def build_authorize_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
        redirect_uri: str,
    ) -> str:
        """Return the URL the browser should be redirected to at /login.

        All security-relevant parameters (state, nonce, PKCE challenge)
        come from the sealed-cookie state — the caller is responsible
        for putting them in the cookie BEFORE generating the URL.
        """
        params: dict[str, str] = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(self._scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            # access_type=offline would give us a refresh_token — we
            # don't want one for login-only flow, and storing tokens
            # we don't use is just liability. Leave it default
            # (=online).
        }
        # Operator-supplied params (hd=, prompt=, login_hint=) layer on
        # top but never override the security-critical fields above.
        for k, v in self._extra_authorize_params.items():
            params.setdefault(k, v)
        return f"{self.AUTHORIZE_ENDPOINT}?{urlencode(params)}"

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        http_client: httpx.AsyncClient,
    ) -> dict[str, Any]:
        """POST the authorization code to the IdP's token endpoint.

        Returns the parsed token response. The caller picks ``id_token``
        out of it and feeds that into the verifier.

        Raises :class:`TokenExchangeError` on network / HTTP / payload
        failures.
        """
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }
        try:
            resp = await http_client.post(
                self.TOKEN_ENDPOINT,
                data=data,
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise TokenExchangeError(
                f"token exchange request failed: {exc.__class__.__name__}: {exc}"
            ) from exc

        if resp.status_code >= 400:
            # Google returns a JSON body with ``error`` and
            # ``error_description``. Don't echo it back to the user —
            # leak the request_id via logs instead.
            raise TokenExchangeError(
                f"token endpoint returned HTTP {resp.status_code}"
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise TokenExchangeError("token response was not JSON") from exc

        if not isinstance(payload, dict) or "id_token" not in payload:
            raise TokenExchangeError(
                "token response missing 'id_token' — IdP did not honour the openid scope"
            )
        return payload
