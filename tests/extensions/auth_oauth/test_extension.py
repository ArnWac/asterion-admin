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

from adminfoundry import CoreAdminConfig, create_admin
from adminfoundry.extensions.auth_oauth import (
    GoogleOIDCProvider,
    OAuthExtension,
)
from adminfoundry.security.protected_fields import reset_for_tests as reset_protected
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
    keys = app.state.adminfoundry.permission_registry.all()
    assert "oauth.identities.list" in keys
    assert "oauth.identities.unlink" in keys


def test_extension_registers_protected_fields(tmp_path):
    _build_app(
        tmp_path,
        providers=[GoogleOIDCProvider(client_id="x", client_secret="y")],
    )
    from adminfoundry.security.protected_fields import get_registry

    pfr = get_registry().as_frozenset()
    assert "access_token" in pfr
    assert "refresh_token" in pfr
    assert "id_token" in pfr
    assert "client_secret" in pfr


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


# --- placeholder routes ---


def test_placeholder_login_returns_501_with_useful_body(tmp_path):
    app = _build_app(
        tmp_path,
        providers=[GoogleOIDCProvider(client_id="x", client_secret="y")],
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/api/v1/oauth/google/login")
    assert resp.status_code == 501
    body = resp.json()
    # The error envelope wraps {"detail": {...}} into the framework's
    # standard error response shape. Either form is acceptable as long
    # as the provider id + phase hint are surfaced.
    text = str(body)
    assert "google" in text
    assert "8a-skeleton" in text or "skeleton" in text


def test_placeholder_callback_returns_501(tmp_path):
    app = _build_app(
        tmp_path,
        providers=[GoogleOIDCProvider(client_id="x", client_secret="y")],
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/api/v1/oauth/google/callback")
    assert resp.status_code == 501


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
