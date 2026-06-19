"""Tests for CORS install logic + unsafe-config rejection."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from asterion import CoreAdminConfig, create_admin


def _make(tmp_path, **overrides):
    return CoreAdminConfig(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'cors.db'}",
        secret_key="test-cors-secret",
        enable_multi_tenant=False,
        enable_builtin_ui=False,
        enable_builtin_admins=False,
        **overrides,
    )


# --- unsafe-config rejection ---


def test_star_origin_with_credentials_rejected(tmp_path):
    cfg = _make(
        tmp_path,
        cors_origins=("*",),
        cors_allow_credentials=True,
    )
    with pytest.raises(ValueError, match="Unsafe CORS"):
        cfg.validate()


def test_create_admin_blocks_unsafe_cors(tmp_path):
    """The factory calls validate() — unsafe config must surface."""
    with pytest.raises(ValueError, match="Unsafe CORS"):
        create_admin(
            config=_make(
                tmp_path,
                cors_origins=("*",),
                cors_allow_credentials=True,
            )
        )


def test_star_origin_without_credentials_allowed(tmp_path):
    _make(tmp_path, cors_origins=("*",), cors_allow_credentials=False).validate()


def test_specific_origin_with_credentials_allowed(tmp_path):
    _make(
        tmp_path,
        cors_origins=("https://app.example.com",),
        cors_allow_credentials=True,
    ).validate()


# --- middleware install ---


def test_cors_middleware_not_installed_when_no_origins(tmp_path):
    app = create_admin(config=_make(tmp_path))
    # If CORS middleware is installed, an OPTIONS preflight would set
    # access-control-allow-origin. Without it, the response is plain.
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.options(
            "/healthz",
            headers={
                "Origin": "https://elsewhere.example",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert "access-control-allow-origin" not in (k.lower() for k in resp.headers)


def test_cors_middleware_serves_allowed_origin(tmp_path):
    app = create_admin(
        config=_make(
            tmp_path,
            cors_origins=("https://app.example.com",),
        )
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.options(
            "/healthz",
            headers={
                "Origin": "https://app.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert resp.headers.get("access-control-allow-origin") == "https://app.example.com"


def test_cors_middleware_does_not_echo_disallowed_origin(tmp_path):
    app = create_admin(
        config=_make(
            tmp_path,
            cors_origins=("https://app.example.com",),
        )
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.options(
            "/healthz",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert resp.headers.get("access-control-allow-origin") != "https://evil.example"
