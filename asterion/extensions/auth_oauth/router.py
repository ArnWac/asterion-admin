"""OAuth/OIDC redirect-flow handlers — Phase 8b.7.

Two endpoints per provider:

* ``GET /api/v1/oauth/{provider_id}/login``
   1. Generate fresh state + nonce + PKCE verifier.
   2. Seal them into the cookie (10-minute TTL, HMAC-SHA256).
   3. Build the IdP authorize URL with the matching ``state`` /
      ``nonce`` / ``code_challenge``.
   4. 302 redirect to the IdP.

* ``GET /api/v1/oauth/{provider_id}/callback``
   1. Read + verify the sealed cookie. Clear it immediately
      (single-use).
   2. Check the ``state`` query param matches the cookie.
   3. POST the authorization code to the token endpoint with the PKCE
      verifier. Reject on any error.
   4. Verify the ID-token end-to-end (sig + iss + aud + exp + nonce).
   5. Find-or-create the user via the extension's
      :class:`OAuthCapableUserProvider`.
   6. Mint a framework access token.
   7. 302 redirect to ``return_to``#token=<jwt> — fragment, not query,
      so the token doesn't leak via referer headers or server logs.

Failures: every error path logs the specific reason via the
extension's logger and redirects to ``<login_path>?oauth_error=<code>``
with a small set of generic codes. The user never sees the underlying
exception; operators correlate via request_id + logs.

The callback handler runs in a single HTTP request, but the work is
sequential by design — each step depends on the previous one
succeeding. No parallelism to chase here.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from asterion.auth.tokens import create_access_token, create_refresh_token
from asterion.extensions.auth_oauth.base import InvalidClaimsError
from asterion.extensions.auth_oauth.providers import (
    GoogleOIDCProvider,
    TokenExchangeError,
)
from asterion.extensions.auth_oauth.state import (
    OAuthFlowState,
    OAuthStateError,
    clear_state_cookie,
    code_challenge_from_verifier,
    cookie_name_for_request,
    generate_code_verifier,
    generate_nonce,
    generate_state,
    seal_state,
    set_state_cookie,
    unseal_state,
)
from asterion.extensions.auth_oauth.user_provider import (
    OAuthCapabilityError,
)
from asterion.extensions.auth_oauth.verifier import (
    IDTokenError,
    verify_id_token,
)

logger = logging.getLogger("asterion.extensions.auth_oauth")

#: Default landing page on success when no ``return_to`` was given.
_DEFAULT_RETURN_TO: str = "/admin/dashboard"

#: Page the UI eventually loads with the JWT in the URL fragment.
#: Phase 8b.8 ships the JS that reads ``#token=…`` and stores it.
_LOGIN_COMPLETE_PATH: str = "/admin/login-complete"

#: Where to send the user on any error — relative path, no host.
_LOGIN_ERROR_PATH: str = "/admin/login"

#: How long the issued framework JWT is valid for. Re-use the
#: framework's existing access-token expiry semantics rather than
#: inventing OAuth-specific tokens.


def _attach_login_handler(
    router: APIRouter,
    provider: GoogleOIDCProvider,
) -> None:
    """Mount the ``/login`` handler for a single provider.

    Defined as a helper (not inline in the factory) so the closure
    over ``provider`` is explicit and the loop variable can't leak —
    the previous bug where ``_provider_id: str = provider_id`` was
    needed in the function signature is avoided.
    """
    provider_id = provider.config.id

    @router.get(f"/{provider_id}/login", name=f"oauth_{provider_id}_login")
    async def _login(request: Request) -> RedirectResponse:
        # ---- generate fresh per-flow secrets ----
        state = generate_state()
        nonce = generate_nonce()
        code_verifier = generate_code_verifier()
        return_to = request.query_params.get("return_to") or _DEFAULT_RETURN_TO
        # Refuse open-redirects: only same-site relative paths allowed
        # in return_to. An absolute URL or anything starting with //
        # would let an attacker bounce the user to an arbitrary host
        # after a successful login.
        if not return_to.startswith("/") or return_to.startswith("//"):
            return_to = _DEFAULT_RETURN_TO

        # ---- seal cookie + build authorize URL ----
        payload = OAuthFlowState(
            state=state,
            code_verifier=code_verifier,
            nonce=nonce,
            provider_id=provider_id,
            return_to=return_to,
            created_at=int(time.time()),
        )
        config = request.app.state.asterion.config
        sealed = seal_state(payload, config.secret_key)
        redirect_uri = _callback_url(request, provider_id)
        authorize_url = provider.build_authorize_url(
            state=state,
            nonce=nonce,
            code_challenge=code_challenge_from_verifier(code_verifier),
            redirect_uri=redirect_uri,
        )

        # 302 (not 307) — semantically a GET → GET redirect. 307 would
        # be wrong for a navigation triggered by a click on a login
        # button.
        response = RedirectResponse(url=authorize_url, status_code=302)
        set_state_cookie(response, sealed, request=request)
        return response


def _attach_callback_handler(
    router: APIRouter,
    provider: GoogleOIDCProvider,
) -> None:
    """Mount the ``/callback`` handler for a single provider."""
    provider_id = provider.config.id

    @router.get(f"/{provider_id}/callback", name=f"oauth_{provider_id}_callback")
    async def _callback(request: Request) -> RedirectResponse:
        runtime = request.app.state.asterion
        ext = _extension_from(request)
        cookie_name = cookie_name_for_request(request)
        raw_cookie = request.cookies.get(cookie_name, "")

        # Every error path goes through this helper so the cookie is
        # ALWAYS cleared and the user gets a consistent redirect with
        # a generic error code. Real diagnostics land in the logs.
        def _fail(code: str, reason: str) -> RedirectResponse:
            logger.warning(
                "oauth callback failed: provider=%s code=%s reason=%s",
                provider_id,
                code,
                reason,
            )
            resp = RedirectResponse(
                url=f"{_LOGIN_ERROR_PATH}?{urlencode({'oauth_error': code})}",
                status_code=302,
            )
            clear_state_cookie(resp, request=request)
            return resp

        # ---- 1. unseal cookie ----
        try:
            flow = unseal_state(raw_cookie, runtime.config.secret_key)
        except OAuthStateError as exc:
            return _fail("state_invalid", str(exc))
        if flow.provider_id != provider_id:
            # Cookie's provider doesn't match this callback URL — could
            # be an attempt to swap callbacks across providers.
            return _fail(
                "state_invalid",
                f"cookie for {flow.provider_id!r} arrived at {provider_id!r}",
            )

        # ---- 2. check state parameter ----
        echoed_state = request.query_params.get("state", "")
        if echoed_state != flow.state:
            return _fail("state_mismatch", "state query param != cookie")

        # ---- 3. catch IdP-reported errors ----
        idp_error = request.query_params.get("error")
        if idp_error:
            return _fail("idp_error", f"IdP returned error={idp_error!r}")

        code = request.query_params.get("code", "")
        if not code:
            return _fail("missing_code", "callback URL missing 'code'")

        # ---- 4. exchange the code for tokens ----
        try:
            token_response = await provider.exchange_code(
                code=code,
                code_verifier=flow.code_verifier,
                redirect_uri=_callback_url(request, provider_id),
                http_client=ext._http_client,
            )
        except TokenExchangeError as exc:
            return _fail("token_exchange_failed", str(exc))

        # ---- 5. verify the ID-token ----
        jwks = ext._jwks_clients.get(provider_id)
        if jwks is None:
            return _fail(
                "internal",
                f"no JWKS client for provider {provider_id!r}",
            )
        try:
            claims = await verify_id_token(
                token_response["id_token"],
                jwks_client=jwks,
                issuer=provider.ISSUER,
                audience=provider.client_id,
                nonce=flow.nonce,
            )
        except IDTokenError as exc:
            return _fail("id_token_invalid", str(exc))

        # ---- 6. map claims + find-or-create user ----
        try:
            identity = provider.claim_mapper(claims)
        except InvalidClaimsError as exc:
            return _fail("claims_invalid", str(exc))

        try:
            principal = await ext._user_provider.find_or_create_by_external_identity(
                provider=identity.provider,
                provider_subject=identity.provider_subject,
                claims=identity,
                allow_create=ext._auto_create_users,
                request=request,
            )
        except OAuthCapabilityError as exc:
            # AutoCreateDisabled / EmailNotVerified / EmailCollision /
            # UserInactive all collapse into one generic code at the
            # wire — the logger has the detail.
            return _fail("user_resolve_failed", str(exc))

        # ---- 7. mint framework JWT pair + redirect with fragment ----
        config = runtime.config

        # Read the user's current token_version from the DB so a
        # previous logout-all on this user keeps invalidating prior
        # tokens (Roadmap 3.5 — pre-3.5 this was hardcoded to 0, which
        # made the OAuth-minted token immortal across logout-all).
        # External-provider subjects (no matching builtin User row)
        # fall back to 0, matching the pre-3.5 semantics for those.
        token_version = await _read_token_version(runtime, principal.id)

        access_token = create_access_token(
            principal.id,
            secret_key=config.secret_key,
            algorithm=config.jwt_algorithm,
            expires_minutes=config.access_token_expire_minutes,
            token_version=token_version,
            issuer=config.jwt_issuer,
            audience=config.jwt_audience,
        )
        refresh_token = create_refresh_token(
            principal.id,
            secret_key=config.secret_key,
            algorithm=config.jwt_algorithm,
            expires_minutes=config.refresh_token_expire_minutes,
            token_version=token_version,
            issuer=config.jwt_issuer,
            audience=config.jwt_audience,
        )

        # Fragment-redirect so the tokens never appear in:
        # * server access logs (request URLs)
        # * referer headers (the next page the JS navigates to)
        # * Sentry / APM URL captures
        # The JS on _LOGIN_COMPLETE_PATH reads location.hash, stores
        # both tokens, then replaces the URL.
        fragment_target = (
            f"{_LOGIN_COMPLETE_PATH}"
            f"#token={quote(access_token, safe='')}"
            f"&refresh={quote(refresh_token, safe='')}"
            f"&return_to={quote(flow.return_to, safe='/')}"
        )
        response = RedirectResponse(url=fragment_target, status_code=302)
        clear_state_cookie(response, request=request)
        return response


def _callback_url(request: Request, provider_id: str) -> str:
    """Build the absolute callback URL the IdP redirects back to.

    Must match exactly what's registered with the IdP — Google rejects
    redirect_uri mismatches at the authorize step. We derive the base
    URL from the inbound request so deployments behind proxies pick
    up the public host without configuration.
    """
    base = str(request.base_url).rstrip("/")
    return f"{base}/api/v1/oauth/{provider_id}/callback"


async def _read_token_version(runtime, principal_id) -> int:
    """Fetch the builtin User's ``token_version`` for the OAuth-resolved
    principal (Roadmap 3.5).

    The OAuth user provider returns an :class:`AdminPrincipal` DTO with
    only ``id`` — no ``token_version``. Without this lookup the
    callback would have to hardcode ``0``, which makes the minted token
    immortal across ``logout-all`` (which bumps tkv). When the
    principal id isn't a UUID (external user provider with opaque ids)
    or doesn't map to a User row, fall back to ``0`` — preserves the
    pre-3.5 semantics for those.
    """
    import uuid as _uuid

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from asterion.models.user import User

    try:
        user_uuid = _uuid.UUID(str(principal_id))
    except (ValueError, AttributeError):
        return 0

    factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
    async with factory() as session:
        value = (
            await session.execute(select(User.token_version).where(User.id == user_uuid))
        ).scalar_one_or_none()
    return int(value) if value is not None else 0


def _extension_from(request: Request):
    """Pull the live :class:`OAuthExtension` instance off the runtime.

    The router needs access to the extension's per-provider JWKS
    clients + the user_provider + the auto_create flag. Walking the
    extension registry is cheap (a tuple scan over typically 1-5
    extensions) and avoids stashing the extension globally.
    """
    runtime = request.app.state.asterion
    # Lazy import — asterion.extensions.auth_oauth.__init__ pulls
    # this module in via build_oauth_router, and top-level importing
    # OAuthExtension would re-enter __init__ during the import.
    from asterion.extensions.auth_oauth import OAuthExtension

    for ext in runtime.extensions:
        if isinstance(ext, OAuthExtension):
            return ext
    raise RuntimeError("build_oauth_router called without a live OAuthExtension on the runtime")


def build_oauth_router(providers: list[GoogleOIDCProvider]) -> APIRouter:
    """Build a sub-router that exposes /login + /callback per provider.

    Called from :meth:`OAuthExtension.register_routes` with the
    extension's configured provider list.
    """
    router = APIRouter(prefix="/oauth", tags=["auth-oauth"])
    for provider in providers:
        _attach_login_handler(router, provider)
        _attach_callback_handler(router, provider)
    return router


__all__ = [
    "build_oauth_router",
]
