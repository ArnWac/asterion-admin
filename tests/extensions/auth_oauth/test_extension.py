"""End-to-end test for the OAuthExtension Phase 8a skeleton.

Validates the architecture promise: a real extension that the framework
has zero knowledge of can be plugged in via ``extensions=[…]`` and:

* registers permission keys → they reach the in-memory registry
* registers protected fields → they reach the protected-field registry
* registers contract contributions → they appear under
  ``/_contract.extensions.auth_oauth.providers``
* mounts placeholder routes → 501 with a useful body

If any of these break, the Phase-5 SPI has regressed and other
extensions would break the same way.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from asterion import CoreAdminConfig, create_admin
from asterion.extensions.auth_oauth import (
    GoogleOIDCProvider,
    OAuthExtension,
)
from asterion.security.protected_fields import reset_for_tests as reset_protected
from tests._helpers import make_admin_principal, override_admin_context


@pytest.fixture(autouse=True)
def _isolate_protected_fields():
    """ProtectedFieldRegistry is a singleton — reset between tests."""
    reset_protected()
    yield
    reset_protected()


def _config(tmp_path) -> CoreAdminConfig:
    return CoreAdminConfig(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'oauth.db'}",
        secret_key="test-oauth",
        enable_multi_tenant=False,
        enable_builtin_ui=False,
        enable_builtin_admins=False,
    )


def _build_app(tmp_path, *, providers=None):
    ext = OAuthExtension(providers=providers or [])
    app = create_admin(config=_config(tmp_path), extensions=[ext])
    override_admin_context(app, principal=make_admin_principal())
    return app


# --- registrations ---


def test_extension_registers_permission_keys(tmp_path):
    app = _build_app(
        tmp_path,
        providers=[GoogleOIDCProvider(client_id="x", client_secret="y")],
    )
    keys = app.state.asterion.permission_registry.all()
    assert "oauth.identities.list" in keys
    assert "oauth.identities.unlink" in keys


def test_extension_does_not_register_speculative_protected_fields(tmp_path):
    """ExternalIdentity stores no tokens (login-only flow). client_secret
    lives on a Python instance attribute, not a model column. So the
    extension intentionally registers NO protected-field names — there's
    nothing for the ProtectedFieldRegistry to mask in serialized output.
    If a future extension grows OAuthCredential or surfaces secrets via
    an admin, it adds register_protected_fields then."""
    _build_app(
        tmp_path,
        providers=[GoogleOIDCProvider(client_id="x", client_secret="y")],
    )
    from asterion.security.protected_fields import get_registry

    pfr = get_registry().as_frozenset()
    assert "access_token" not in pfr
    assert "refresh_token" not in pfr
    assert "id_token" not in pfr
    assert "client_secret" not in pfr


def test_extension_contributes_to_contract(tmp_path):
    app = _build_app(
        tmp_path,
        providers=[
            GoogleOIDCProvider(client_id="x", client_secret="y"),
        ],
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        body = c.get("/api/v1/admin/_contract").json()
    assert "auth_oauth" in body["extensions"]
    providers = body["extensions"]["auth_oauth"]["providers"]
    assert providers == [
        {
            "id": "google",
            "label": "Google",
            "login_url": "/api/v1/oauth/google/login",
        }
    ]


def test_extension_contract_supports_multiple_providers(tmp_path):
    """Two providers, two entries — order preserved."""
    app = _build_app(
        tmp_path,
        providers=[
            GoogleOIDCProvider(
                client_id="x",
                client_secret="y",
                id="google_workspace",
                label="Google Workspace",
            ),
            GoogleOIDCProvider(
                client_id="a",
                client_secret="b",
                id="google_personal",
                label="Google Personal",
            ),
        ],
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        body = c.get("/api/v1/admin/_contract").json()
    ids = [p["id"] for p in body["extensions"]["auth_oauth"]["providers"]]
    assert ids == ["google_workspace", "google_personal"]


# --- real flow routes (Phase 8b.7 smoke tests; deep flow tests live
# in test_router_flow.py) ---


def test_login_redirects_to_idp_authorize_endpoint(tmp_path):
    """Phase 8b: /login generates state + PKCE, sets sealed cookie, 302s
    to Google's authorize endpoint with all the right parameters."""
    app = _build_app(
        tmp_path,
        providers=[GoogleOIDCProvider(client_id="x", client_secret="y")],
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/api/v1/oauth/google/login", follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith(GoogleOIDCProvider.AUTHORIZE_ENDPOINT)
    # The security-critical params must appear unencrypted in the
    # query string for the IdP to read them.
    assert "client_id=x" in location
    assert "response_type=code" in location
    assert "code_challenge_method=S256" in location
    assert "state=" in location
    assert "nonce=" in location
    # Sealed-cookie state was set.
    assert "set-cookie" in {h.lower() for h in resp.headers.keys()}


def test_callback_without_cookie_redirects_to_login_with_error(tmp_path):
    """Phase 8b: the callback handler never throws — every failure path
    redirects to /admin/login?oauth_error=<code> after clearing the
    cookie. Hitting /callback fresh (no cookie) is the simplest failure
    to assert against."""
    app = _build_app(
        tmp_path,
        providers=[GoogleOIDCProvider(client_id="x", client_secret="y")],
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get(
            "/api/v1/oauth/google/callback?state=anything&code=any",
            follow_redirects=False,
        )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("/admin/login")
    assert "oauth_error=state_invalid" in location


def test_no_routes_mounted_when_no_providers(tmp_path):
    """An OAuthExtension with no providers is legal — produces an empty
    contract fragment and zero routes."""
    app = _build_app(tmp_path, providers=[])
    with TestClient(app, raise_server_exceptions=False) as c:
        body = c.get("/api/v1/admin/_contract").json()
        # Login endpoint should not exist.
        resp = c.get("/api/v1/oauth/google/login")
    assert body["extensions"]["auth_oauth"]["providers"] == []
    assert resp.status_code == 404


# --- duplicate provider ids ---


def test_duplicate_provider_ids_rejected_at_construction():
    with pytest.raises(ValueError, match="duplicate provider id"):
        OAuthExtension(
            providers=[
                GoogleOIDCProvider(client_id="x", client_secret="y", id="google"),
                GoogleOIDCProvider(client_id="a", client_secret="b", id="google"),
            ]
        )


# --- provider validation ---


def test_provider_rejects_empty_client_credentials():
    with pytest.raises(ValueError, match="client_id"):
        GoogleOIDCProvider(client_id="", client_secret="y")
    with pytest.raises(ValueError, match="client_id"):
        GoogleOIDCProvider(client_id="x", client_secret="")


def test_provider_repr_is_secret_safe():
    p = GoogleOIDCProvider(client_id="public-client-id", client_secret="SECRET")
    text = repr(p)
    assert "SECRET" not in text
    assert "public-client-id" not in text
