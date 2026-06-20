"""Single-tenant admin access requires superadmin by default (bugfix).

With no tenant context there is no tenant-role/permission system to authorize
against, so the admin surface must not be open to any authenticated account.
``single_tenant_require_superadmin`` (default True) gates it to superadmins;
setting it False restores the legacy "any authenticated caller" behaviour.

This runs the real auth path (login -> token -> request) so the config flag and
``ctx.request`` are exercised end to end — unlike the dependency-override tests.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import String
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from asterion import CoreAdminConfig, ModelAdmin, create_admin
from asterion.auth.password import hash_password
from asterion.models.base import GlobalModel
from asterion.models.user import User

SECRET = "test-single-tenant-gate"
PASSWORD = "hunter2-strong"


class GatePost(GlobalModel):
    __tablename__ = "gate_posts"
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")


class GatePostAdmin(ModelAdmin):
    model = GatePost
    list_display = ["id", "title"]


def _build(tmp_path, *, require_superadmin: bool):
    app = create_admin(
        config=CoreAdminConfig(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'gate.db'}",
            secret_key=SECRET,
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
            single_tenant_require_superadmin=require_superadmin,
        ),
        register=lambda reg: reg.register(GatePostAdmin),
    )
    runtime = app.state.asterion

    async def _setup():
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                session.add(
                    User(
                        email="root@example.com",
                        hashed_password=hash_password(PASSWORD),
                        is_active=True,
                        is_superadmin=True,
                    )
                )
                session.add(
                    User(
                        email="user@example.com",
                        hashed_password=hash_password(PASSWORD),
                        is_active=True,
                        is_superadmin=False,
                    )
                )

    asyncio.run(_setup())
    return app


def _token(client, email):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _list(client, token):
    return client.get("/api/v1/admin/gate_posts", headers={"Authorization": f"Bearer {token}"})


@pytest.fixture
def gated_client(tmp_path):
    with TestClient(_build(tmp_path, require_superadmin=True), raise_server_exceptions=False) as c:
        yield c


def test_superadmin_can_access(gated_client):
    assert _list(gated_client, _token(gated_client, "root@example.com")).status_code == 200


def test_non_superadmin_is_forbidden(gated_client):
    # The core of the fix: a normal authenticated account is NOT a single-tenant
    # admin just by being logged in.
    assert _list(gated_client, _token(gated_client, "user@example.com")).status_code == 403


def test_opt_out_allows_any_authenticated_caller(tmp_path):
    with TestClient(
        _build(tmp_path, require_superadmin=False), raise_server_exceptions=False
    ) as client:
        assert _list(client, _token(client, "user@example.com")).status_code == 200
