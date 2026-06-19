"""TenantMiddleware emits the consistent error envelope (PR-11 hotfix #2)."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion import CoreAdminConfig, create_admin
from asterion.core.errors import FORBIDDEN, NOT_FOUND
from asterion.core.middleware import REQUEST_ID_HEADER
from asterion.models.base import GlobalModel
from asterion.models.tenant import Tenant


@pytest.fixture
def app(tmp_path):
    application = create_admin(
        config=CoreAdminConfig(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'mw-env.db'}",
            secret_key="test-mw-envelope-secret",
            enable_multi_tenant=True,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        )
    )
    runtime = application.state.asterion

    async def _setup():
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                session.add(
                    Tenant(
                        id=uuid.uuid4(),
                        name="Acme",
                        slug="acme",
                        schema_name="tenant_acme",
                        is_active=True,
                    )
                )
                session.add(
                    Tenant(
                        id=uuid.uuid4(),
                        name="Beta",
                        slug="beta",
                        schema_name="tenant_beta",
                        is_active=False,
                    )
                )

    asyncio.run(_setup())
    yield application
    asyncio.run(runtime.db.dispose())


@pytest.fixture
def client(app):
    # Clear the in-process tenant resolver cache between tests.
    from asterion.tenancy.resolver import clear_tenant_cache

    clear_tenant_cache()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _envelope(resp):
    return resp.json()["error"]


# --- unknown tenant ---


def test_unknown_tenant_returns_envelope_with_not_found_code(client):
    resp = client.get("/healthz", headers={"X-Tenant-Slug": "ghost"})
    assert resp.status_code == 404
    err = _envelope(resp)
    assert err["code"] == NOT_FOUND
    assert "ghost" in err["message"]
    # request_id is part of the envelope contract
    assert "request_id" in err


def test_unknown_tenant_carries_request_id_through(client):
    resp = client.get(
        "/healthz",
        headers={"X-Tenant-Slug": "ghost", REQUEST_ID_HEADER: "trace-9"},
    )
    assert _envelope(resp)["request_id"] == "trace-9"


# --- disabled tenant ---


def test_disabled_tenant_returns_envelope_with_forbidden_code(client):
    resp = client.get("/healthz", headers={"X-Tenant-Slug": "beta"})
    assert resp.status_code == 403
    err = _envelope(resp)
    assert err["code"] == FORBIDDEN
    assert "beta" in err["message"]


# --- happy path ---


def test_known_active_tenant_passes_through(client):
    resp = client.get("/healthz", headers={"X-Tenant-Slug": "acme"})
    assert resp.status_code == 200


def test_request_without_tenant_header_passes(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200


# --- no legacy "detail" shape leaks ---


def test_envelope_does_not_fall_back_to_legacy_detail_key(client):
    resp = client.get("/healthz", headers={"X-Tenant-Slug": "ghost"})
    body = resp.json()
    assert "error" in body
    assert "detail" not in body
