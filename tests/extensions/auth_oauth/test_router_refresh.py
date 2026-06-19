"""OAuth callback now mints token PAIR + reads real token_version (3.5).

Pre-3.5 the callback minted only an access token and hardcoded
``token_version=0``. After 3.5:

* the fragment carries ``token=...&refresh=...&return_to=...`` so the
  UI lands with a usable refresh token (parity with the password
  login since 3.1);
* ``token_version`` is read from the builtin User row, so a prior
  ``/logout-all`` keeps invalidating OAuth-minted tokens.
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import unquote

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jose import jwk as jose_jwk
from jose import jwt as jose_jwt
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion import CoreAdminConfig, create_admin
from asterion.auth.tokens import decode_access_token, decode_refresh_token
from asterion.extensions.auth_oauth import GoogleOIDCProvider, OAuthExtension
from asterion.extensions.auth_oauth.state import OAuthFlowState, seal_state
from asterion.models.base import GlobalModel
from asterion.models.user import User
from asterion.security.protected_fields import reset_for_tests as reset_protected

_CLIENT_ID = "test-client-id"
_KID = "test-kid"
SECRET = "test-flow-secret"
ALG = "HS256"


@pytest.fixture(autouse=True)
def _isolate_protected_fields():
    reset_protected()
    yield
    reset_protected()


def _generate_keypair() -> tuple[str, dict]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    public_jwk = jose_jwk.construct(public_pem, algorithm="RS256").to_dict()
    public_jwk["kid"] = _KID
    public_jwk["alg"] = "RS256"
    public_jwk["use"] = "sig"
    return private_pem, public_jwk


def _mint_id_token(private_pem: str, *, nonce: str, email: str) -> str:
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": _CLIENT_ID,
        "azp": _CLIENT_ID,
        "sub": "google-sub-456",
        "exp": now + 600,
        "iat": now,
        "nonce": nonce,
        "email": email,
        "email_verified": True,
        "name": "Alice Anderson",
    }
    return jose_jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": _KID})


def _build_app(tmp_path):
    provider = GoogleOIDCProvider(client_id=_CLIENT_ID, client_secret="test-secret")
    ext = OAuthExtension(providers=[provider], auto_create_users=True)
    app = create_admin(
        config=CoreAdminConfig(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'oauth_refresh.db'}",
            secret_key=SECRET,
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        ),
        extensions=[ext],
    )
    runtime = app.state.asterion

    async def _setup():
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)

    asyncio.run(_setup())
    return app, ext


def _patch_http(ext, handler):
    transport = httpx.MockTransport(handler)
    new_client = httpx.AsyncClient(transport=transport)
    ext._http_client = new_client
    for jwks in ext._jwks_clients.values():
        jwks._http_client = new_client
        jwks._owns_client = False


def _seal_cookie(app, *, state, nonce, code_verifier):
    payload = OAuthFlowState(
        state=state,
        code_verifier=code_verifier,
        nonce=nonce,
        provider_id="google",
        return_to="/admin/dashboard",
        created_at=int(time.time()),
    )
    return seal_state(payload, app.state.asterion.config.secret_key)


def _do_callback(tmp_path, *, email: str = "alice@example.com"):
    """Walk a single OAuth callback through, return (app, redirect_location)."""
    app, ext = _build_app(tmp_path)
    priv, pub = _generate_keypair()
    state = "test-state"
    nonce = "test-nonce"
    code_verifier = "test-verifier"
    id_token = _mint_id_token(priv, nonce=nonce, email=email)
    cookie = _seal_cookie(app, state=state, nonce=nonce, code_verifier=code_verifier)

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth2.googleapis.com/token" in str(request.url):
            return httpx.Response(
                200,
                json={"id_token": id_token, "access_token": "ignored", "token_type": "Bearer"},
            )
        if "googleapis.com/oauth2/v3/certs" in str(request.url):
            return httpx.Response(200, json={"keys": [pub]})
        return httpx.Response(404)

    with TestClient(app, raise_server_exceptions=False) as c:
        _patch_http(ext, handler)
        c.cookies.set("asterion_oauth_state", cookie)
        resp = c.get(
            f"/api/v1/oauth/google/callback?state={state}&code=mocked-code",
            follow_redirects=False,
        )
    assert resp.status_code == 302
    return app, resp.headers["location"]


def _extract_fragment(location: str) -> dict[str, str]:
    """Pull #token=...&refresh=...&return_to=... into a dict."""
    assert "#" in location
    fragment = location.split("#", 1)[1]
    out: dict[str, str] = {}
    for part in fragment.split("&"):
        k, _, v = part.partition("=")
        out[k] = unquote(v)
    return out


# ---------------------------------------------------------------------------
# Fragment now carries token + refresh
# ---------------------------------------------------------------------------


def test_callback_fragment_carries_refresh_token(tmp_path):
    """Roadmap 3.5 — the fragment now includes a refresh_token alongside
    the access token. Pre-3.5 only ``token`` was present; the UI had no
    way to silently re-acquire access after expiry."""
    _, location = _do_callback(tmp_path)
    parts = _extract_fragment(location)
    assert "token" in parts
    assert "refresh" in parts
    assert parts["token"] != parts["refresh"]


def test_callback_refresh_token_is_valid_refresh_jwt(tmp_path):
    """Pin the refresh-token shape: signed by the framework secret,
    type=refresh, same subject as the access token."""
    _, location = _do_callback(tmp_path)
    parts = _extract_fragment(location)

    access = decode_access_token(
        parts["token"],
        secret_key=SECRET,
        algorithm=ALG,
    )
    refresh = decode_refresh_token(
        parts["refresh"],
        secret_key=SECRET,
        algorithm=ALG,
    )
    assert access["type"] == "access"
    assert refresh["type"] == "refresh"
    assert access["sub"] == refresh["sub"]


def test_callback_refresh_can_be_exchanged_via_auth_refresh(tmp_path):
    """End-to-end: the OAuth-issued refresh token works at
    ``/auth/refresh`` the same way the password-login refresh does."""
    app, location = _do_callback(tmp_path)
    refresh = _extract_fragment(location)["refresh"]
    resp = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["refresh_token"] != refresh  # rotation


# ---------------------------------------------------------------------------
# token_version is read from the DB
# ---------------------------------------------------------------------------


def test_callback_uses_users_actual_token_version(tmp_path):
    """Pre-3.5 token_version was hardcoded 0, which made the OAuth token
    immortal across /logout-all. After 3.5 the callback reads the live
    value from the User row."""
    app, ext = _build_app(tmp_path)
    runtime = app.state.asterion

    # Seed a user with a pre-bumped token_version BEFORE the OAuth callback
    # finds them. The auto-create branch normally creates fresh users with
    # tkv=0; this test pre-creates one + the external_identity link so the
    # callback hits the "found existing" branch.
    from asterion.auth.password import hash_password
    from asterion.extensions.auth_oauth.models import ExternalIdentity

    user_id: dict = {}

    async def _seed():
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                u = User(
                    email="prebumped@example.com",
                    hashed_password=hash_password("placeholder-strong"),
                    is_active=True,
                    token_version=7,  # ← simulating a prior /logout-all bump
                )
                session.add(u)
            await session.refresh(u)
            user_id["id"] = u.id
            async with factory() as link_session:
                async with link_session.begin():
                    link_session.add(
                        ExternalIdentity(
                            user_id=u.id,
                            provider="google",
                            provider_subject="google-sub-456",
                            email_at_provider=u.email,
                        )
                    )

    asyncio.run(_seed())

    priv, pub = _generate_keypair()
    state = "test-state"
    nonce = "test-nonce"
    code_verifier = "test-verifier"
    id_token = _mint_id_token(priv, nonce=nonce, email="prebumped@example.com")
    cookie = _seal_cookie(app, state=state, nonce=nonce, code_verifier=code_verifier)

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth2.googleapis.com/token" in str(request.url):
            return httpx.Response(
                200,
                json={"id_token": id_token, "access_token": "ignored", "token_type": "Bearer"},
            )
        if "googleapis.com/oauth2/v3/certs" in str(request.url):
            return httpx.Response(200, json={"keys": [pub]})
        return httpx.Response(404)

    with TestClient(app, raise_server_exceptions=False) as c:
        _patch_http(ext, handler)
        c.cookies.set("asterion_oauth_state", cookie)
        resp = c.get(
            f"/api/v1/oauth/google/callback?state={state}&code=mocked-code",
            follow_redirects=False,
        )

    assert resp.status_code == 302
    parts = _extract_fragment(resp.headers["location"])
    access = decode_access_token(parts["token"], secret_key=SECRET, algorithm=ALG)
    # The minted token carries the user's CURRENT tkv (7), not 0.
    assert access["tkv"] == 7


def test_oauth_token_invalidated_by_logout_all(tmp_path):
    """Roadmap 3.5 — proves the token_version fix is wired: an OAuth-
    minted access token is invalidated when the user calls
    /logout-all (which bumps tkv on the User row)."""
    app, location = _do_callback(tmp_path)
    token = _extract_fragment(location)["token"]
    headers = {"Authorization": f"Bearer {token}"}
    client = TestClient(app, raise_server_exceptions=False)

    # Token works first.
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200
    # logout-all bumps tkv.
    assert client.post("/api/v1/auth/logout-all", headers=headers).status_code == 200
    # OAuth-minted token is now stale (this would have been
    # impossible pre-3.5 — tkv was hardcoded 0 so logout-all couldn't
    # touch it).
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
