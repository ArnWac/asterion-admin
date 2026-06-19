"""Tests for /healthz and /readyz."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from asterion import CoreAdminConfig, create_admin


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'health.db'}"
    return create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key="test-health-secret",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        )
    )


@pytest.fixture
def client(app):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# --- /healthz ---


def test_healthz_returns_200(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_healthz_does_not_touch_database(app):
    """Liveness must not depend on DB. We dispose the engine and confirm
    the endpoint still returns 200."""
    import asyncio

    asyncio.run(app.state.asterion.db.dispose())
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200


# --- /readyz ---


def test_readyz_ok_when_db_reachable(client):
    resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"


def test_readyz_503_when_db_unreachable(tmp_path):
    """Point the manager at a sqlite file in a non-existent directory."""
    bad_url = f"sqlite+aiosqlite:///{tmp_path}/no/such/dir/x.db"
    app = create_admin(
        config=CoreAdminConfig(
            database_url=bad_url,
            secret_key="test-health-bad-db",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        )
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["db"] != "ok"
