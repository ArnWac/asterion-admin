"""Service / machine account provisioning (create_service_account).

The provider-resolution acceptance ("a token for this account yields a
principal with exactly the granted keys") needs a real tenant schema and lives
in tests/postgres/test_service_accounts.py. Here we cover the DB-agnostic
behaviour on SQLite plus the HTTP auth invariants (login rejects it; a minted
token authenticates and a token_version bump invalidates it).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from asterion import CoreAdminConfig, create_admin
from asterion.auth.service_accounts import create_service_account
from asterion.auth.tokens import create_access_token
from asterion.models.base import GLOBAL_METADATA, TenantBase
from asterion.models.tenant_membership import TenantMembership
from asterion.models.tenant_rbac import (
    TenantMembershipRole,
    TenantRole,
    TenantRolePermission,
)
from asterion.models.user import User
from asterion.security.validation import InvalidPermissionKeyError

SECRET = "x" * 64


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        execution_options={"schema_translate_map": {"public": None}},
    )
    async with engine.begin() as conn:
        await conn.run_sync(GLOBAL_METADATA.create_all)
        await conn.run_sync(TenantBase.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


# --- provisioning ---


@pytest.mark.asyncio
async def test_provisions_passwordless_active_user_with_role(factory):
    tenant_id = uuid.uuid4()
    async with factory() as s:
        async with s.begin():
            user = await create_service_account(
                s,
                tenant_id=tenant_id,
                label="lobby-terminal",
                permission_keys=["admin.time_entries.create", "admin.time_entries.read"],
            )
            uid = user.id
            # Active, non-superadmin, passwordless (hash set but unusable).
            assert user.is_active is True
            assert user.is_superadmin is False
            assert user.hashed_password
            assert user.email.endswith("@service.invalid")

    async with factory() as s:
        membership = (
            await s.execute(select(TenantMembership).where(TenantMembership.user_id == uid))
        ).scalar_one()
        assert membership.tenant_id == tenant_id
        assert membership.is_active is True

        role = (
            await s.execute(select(TenantRole).where(TenantRole.name == "service:lobby-terminal"))
        ).scalar_one()
        assert role.is_system is False

        grants = {
            r.permission_key
            for r in (
                await s.execute(
                    select(TenantRolePermission).where(TenantRolePermission.role_id == role.id)
                )
            )
            .scalars()
            .all()
        }
        assert grants == {"admin.time_entries.create", "admin.time_entries.read"}

        link = (
            await s.execute(
                select(TenantMembershipRole).where(
                    TenantMembershipRole.membership_id == membership.id,
                    TenantMembershipRole.role_id == role.id,
                )
            )
        ).scalar_one_or_none()
        assert link is not None


@pytest.mark.asyncio
async def test_duplicate_email_raises(factory):
    tid = uuid.uuid4()
    async with factory() as s:
        async with s.begin():
            await create_service_account(
                s, tenant_id=tid, label="t1", permission_keys=[], email="dev@example.com"
            )
    async with factory() as s:
        async with s.begin():
            with pytest.raises(ValueError, match="already exists"):
                await create_service_account(
                    s, tenant_id=tid, label="t2", permission_keys=[], email="dev@example.com"
                )


@pytest.mark.asyncio
async def test_invalid_permission_key_raises(factory):
    async with factory() as s:
        async with s.begin():
            with pytest.raises(InvalidPermissionKeyError):
                await create_service_account(
                    s, tenant_id=uuid.uuid4(), label="t", permission_keys=["NOT A KEY"]
                )


@pytest.mark.asyncio
async def test_duplicate_label_raises(factory):
    tid = uuid.uuid4()
    async with factory() as s:
        async with s.begin():
            await create_service_account(s, tenant_id=tid, label="dup", permission_keys=[])
    async with factory() as s:
        async with s.begin():
            # Different synthetic email, but the service:<label> role clashes.
            with pytest.raises(ValueError, match="already exists"):
                await create_service_account(s, tenant_id=tid, label="dup", permission_keys=[])


# --- HTTP auth invariants ---


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'svc.db'}"
    application = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key=SECRET,
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        ),
    )
    runtime = application.state.asterion
    state: dict = {}

    async def _setup():
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GLOBAL_METADATA.create_all)
            await conn.run_sync(TenantBase.metadata.create_all)
        async with runtime.db.session() as session:
            async with session.begin():
                user = await create_service_account(
                    session,
                    tenant_id=uuid.uuid4(),
                    label="terminal",
                    permission_keys=["admin.time_entries.create"],
                    email="terminal@example.com",
                )
                state["uid"] = user.id
                state["email"] = user.email
                state["tkv"] = user.token_version

    asyncio.run(_setup())
    yield application, runtime, state
    asyncio.run(runtime.db.dispose())


def test_login_rejects_passwordless_service_account(app):
    application, _, state = app
    resp = TestClient(application).post(
        "/api/v1/auth/login",
        json={"email": state["email"], "password": "whatever-strong-1"},
    )
    assert resp.status_code == 401, resp.text


def test_minted_token_authenticates_then_bump_invalidates(app):
    application, runtime, state = app
    cfg = runtime.config
    token = create_access_token(
        state["uid"],
        secret_key=cfg.secret_key,
        algorithm=cfg.jwt_algorithm,
        expires_minutes=cfg.access_token_expire_minutes,
        token_version=state["tkv"],
    )
    client = TestClient(application)
    headers = {"Authorization": f"Bearer {token}"}

    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json()["email"] == state["email"]

    async def _bump():
        async with runtime.db.session() as session:
            async with session.begin():
                user = (
                    await session.execute(select(User).where(User.id == state["uid"]))
                ).scalar_one()
                user.token_version = (user.token_version or 0) + 1

    asyncio.run(_bump())

    me2 = client.get("/api/v1/auth/me", headers=headers)
    assert me2.status_code == 401
