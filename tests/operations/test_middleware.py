"""Tests for RequestIDMiddleware + SecurityHeadersMiddleware."""

from __future__ import annotations

import pytest
from fastapi import Request  # imported at module level so FastAPI's
from fastapi.testclient import TestClient  # forward-ref resolver finds it

from asterion import CoreAdminConfig, create_admin
from asterion.core.middleware import REQUEST_ID_HEADER


@pytest.fixture
def app(tmp_path):
    return create_admin(
        config=CoreAdminConfig(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'mw.db'}",
            secret_key="test-mw-secret",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        )
    )


@pytest.fixture
def client(app):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# --- request id ---


def test_response_carries_generated_request_id(client):
    resp = client.get("/healthz")
    rid = resp.headers.get(REQUEST_ID_HEADER)
    assert rid
    assert len(rid) >= 16


def test_response_echoes_inbound_request_id(client):
    resp = client.get("/healthz", headers={REQUEST_ID_HEADER: "abc-123"})
    assert resp.headers[REQUEST_ID_HEADER] == "abc-123"


def test_each_request_gets_unique_id_when_unset(client):
    a = client.get("/healthz").headers[REQUEST_ID_HEADER]
    b = client.get("/healthz").headers[REQUEST_ID_HEADER]
    assert a != b


def test_request_id_available_on_request_state(app):
    """Add a probe route that reads request.state.request_id."""

    @app.get("/probe-request-id")
    async def probe(request: Request):
        return {"rid": request.state.request_id}

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/probe-request-id", headers={REQUEST_ID_HEADER: "xyz"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["rid"] == "xyz"


# --- security headers ---


def test_security_headers_present_by_default(client):
    resp = client.get("/healthz")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    assert resp.headers["X-Frame-Options"] == "DENY"


def test_security_headers_disabled_when_config_off(tmp_path):
    app = create_admin(
        config=CoreAdminConfig(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'no-sec.db'}",
            secret_key="test-no-sec",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
            security_headers_enabled=False,
        )
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/healthz")
    assert "X-Content-Type-Options" not in resp.headers


# --- CSP (Review R14) ---


def test_no_csp_header_by_default(client):
    # The bundled UI uses inline scripts; no CSP is emitted unless configured.
    resp = client.get("/healthz")
    assert "Content-Security-Policy" not in resp.headers


def test_csp_header_emitted_when_configured(tmp_path):
    policy = "default-src 'self'; frame-ancestors 'none'"
    app = create_admin(
        config=CoreAdminConfig(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'csp.db'}",
            secret_key="test-csp",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
            content_security_policy=policy,
        )
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/healthz")
    assert resp.headers["Content-Security-Policy"] == policy
