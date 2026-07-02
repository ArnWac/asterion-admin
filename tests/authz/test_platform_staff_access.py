"""Platform staff can log in at shared scope with graded rights (ADR-0004).

A user with a ``PlatformRole`` holding a scoped ``platform.*`` key reaches a
``superadmin_only`` admin for exactly the actions that key allows — no full
``is_superadmin`` needed. A plain authenticated user (no platform role) is still
refused. Runs the real auth path (login -> token -> request) so the provider's
shared-scope platform-role lookup is exercised end to end.
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
from asterion.models.platform_rbac import PlatformRole, PlatformRolePermission, PlatformUserRole
from asterion.models.user import User

SECRET = "test-platform-staff"
PASSWORD = "hunter2-strong"


class StaffPost(GlobalModel):
    __tablename__ = "staff_posts"
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")


class StaffPostAdmin(ModelAdmin):
    """A platform-tier resource: only the platform tier may reach it."""

    model = StaffPost
    superadmin_only = True
    list_display = ["id", "title"]


def _build(tmp_path):
    app = create_admin(
        config=CoreAdminConfig(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'staff.db'}",
            secret_key=SECRET,
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
            single_tenant_require_superadmin=True,
        ),
        register=lambda reg: reg.register(StaffPostAdmin),
    )
    runtime = app.state.asterion

    async def _setup():
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                staff = User(
                    email="staff@example.com",
                    hashed_password=hash_password(PASSWORD),
                    is_active=True,
                    is_superadmin=False,
                )
                session.add(staff)
                session.add(
                    User(
                        email="plain@example.com",
                        hashed_password=hash_password(PASSWORD),
                        is_active=True,
                        is_superadmin=False,
                    )
                )
                await session.flush()
                # A "support" platform role that may only LIST staff posts.
                role = PlatformRole(name="support", description="read-only staff")
                session.add(role)
                await session.flush()
                session.add(
                    PlatformRolePermission(
                        role_id=role.id, permission_key="platform.staff_posts.list"
                    )
                )
                session.add(PlatformUserRole(user_id=staff.id, role_id=role.id))

    asyncio.run(_setup())
    return app


def _token(client, email):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(tmp_path):
    with TestClient(_build(tmp_path), raise_server_exceptions=False) as c:
        yield c


def test_scoped_staff_can_list_via_platform_key(client):
    token = _token(client, "staff@example.com")
    resp = client.get("/api/v1/admin/staff_posts", headers=_headers(token))
    assert resp.status_code == 200, resp.text


def test_scoped_staff_cannot_create_without_the_key(client):
    """The role holds only ``platform.staff_posts.list`` — create is refused."""
    token = _token(client, "staff@example.com")
    resp = client.post(
        "/api/v1/admin/staff_posts", json={"title": "x"}, headers=_headers(token)
    )
    assert resp.status_code == 403, resp.text


def test_plain_user_without_platform_role_is_forbidden(client):
    token = _token(client, "plain@example.com")
    resp = client.get("/api/v1/admin/staff_posts", headers=_headers(token))
    assert resp.status_code == 403, resp.text
