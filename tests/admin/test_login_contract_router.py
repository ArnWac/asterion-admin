"""Tests for /api/v1/admin/_login_contract — Phase 8b.8.

This endpoint is deliberately public (no auth) so the login page can
fetch it before the user is signed in. The tests assert two things:

1. **The shape is stable** even when the OAuth extension isn't
   installed — UI code shouldn't have to special-case its absence.
2. **It does NOT leak anything other than OAuth provider buttons.**
   The full /_contract endpoint is auth-gated for a reason; widening
   that contract via this back door would defeat the point of the
   separate endpoint.
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


@pytest.fixture(autouse=True)
def _isolate_protected_fields():
    reset_protected()
    yield
    reset_protected()


def _config(tmp_path) -> CoreAdminConfig:
    return CoreAdminConfig(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'lc.db'}",
        secret_key="test-login-contract",
        enable_multi_tenant=False,
        enable_builtin_ui=False,
        enable_builtin_admins=False,
    )


# --- public access ---


def test_endpoint_is_anonymous_readable(tmp_path):
    """The whole point of the endpoint: works without an Authorization header."""
    app = create_admin(config=_config(tmp_path))
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/api/v1/admin/_login_contract")
    assert resp.status_code == 200


def test_returns_empty_list_when_oauth_not_installed(tmp_path):
    """Shape is stable — empty list, not 404 or missing key."""
    app = create_admin(config=_config(tmp_path))
    with TestClient(app, raise_server_exceptions=False) as c:
        body = c.get("/api/v1/admin/_login_contract").json()
    assert body == {"oauth_providers": []}


def test_returns_configured_oauth_providers(tmp_path):
    app = create_admin(
        config=_config(tmp_path),
        extensions=[
            OAuthExtension(
                providers=[
                    GoogleOIDCProvider(client_id="x", client_secret="y"),
                ]
            )
        ],
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        body = c.get("/api/v1/admin/_login_contract").json()
    assert body == {
        "oauth_providers": [
            {"id": "google", "label": "Google", "login_url": "/api/v1/oauth/google/login"},
        ]
    }


def test_handles_multiple_providers_in_order(tmp_path):
    app = create_admin(
        config=_config(tmp_path),
        extensions=[
            OAuthExtension(
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
                ]
            )
        ],
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        ids = [p["id"] for p in c.get("/api/v1/admin/_login_contract").json()["oauth_providers"]]
    assert ids == ["google_workspace", "google_personal"]


# --- non-leakage guarantees ---


def test_response_contains_only_oauth_providers_key(tmp_path):
    """If we ever accidentally add another top-level key, this test
    catches it — the public endpoint's surface should stay narrow."""
    app = create_admin(
        config=_config(tmp_path),
        extensions=[
            OAuthExtension(
                providers=[
                    GoogleOIDCProvider(client_id="x", client_secret="y"),
                ]
            )
        ],
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        body = c.get("/api/v1/admin/_login_contract").json()
    assert set(body.keys()) == {"oauth_providers"}


def test_provider_response_only_contains_three_fields(tmp_path):
    """The OAuth extension's contract contribution may grow more
    fields (e.g. for an admin UI). This endpoint MUST keep filtering
    to the three the login page actually needs."""
    app = create_admin(
        config=_config(tmp_path),
        extensions=[
            OAuthExtension(
                providers=[
                    GoogleOIDCProvider(client_id="x", client_secret="y"),
                ]
            )
        ],
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        provider = c.get("/api/v1/admin/_login_contract").json()["oauth_providers"][0]
    assert set(provider.keys()) == {"id", "label", "login_url"}


def test_does_not_expose_model_admins(tmp_path):
    """Sanity check — /_contract leaks model names; /_login_contract
    must not. The full contract has 'models' as a top-level key; the
    login contract must not."""
    app = create_admin(config=_config(tmp_path))
    with TestClient(app, raise_server_exceptions=False) as c:
        body = c.get("/api/v1/admin/_login_contract").json()
    assert "models" not in body
    assert "extensions" not in body
    assert "contract_version" not in body
