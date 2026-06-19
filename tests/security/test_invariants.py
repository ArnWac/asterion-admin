"""S5 — Security Invariant Suite.

One test per item on the v1-core roadmap §Phase S5 list. The point of
this file is traceability: each test maps 1:1 to a roadmap bullet, with
a short docstring explaining the invariant. Wider behavioural coverage
for each topic lives in its dedicated test file (auth_invariants,
crud_field_protection, etc.); failures here mean a security boundary
declared as core has slipped.

S5 checklist (verbatim from the roadmap):
    1. hidden fields never serialized
    2. hashed_password never serialized
    3. readonly fields not writable
    4. inactive user rejected
    5. token_version mismatch rejected
    6. impersonation token rejected by require_superadmin
    7. unknown permission denied
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Boolean, Column, Integer, String
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from asterion import CoreAdminConfig, ModelAdmin, create_admin
from asterion.auth.password import hash_password
from asterion.auth.tokens import (
    create_access_token,
    create_impersonation_token,
)
from asterion.authz.permissions import (
    assert_permission,
    has_permission,
    permission_key,
)
from asterion.crud.payload import clean_write_payload
from asterion.models.base import GlobalModel
from asterion.models.user import User
from asterion.schemas.builder import build_model_schema
from asterion.schemas.serialization.serializer import serialize_record
from tests._helpers import make_admin_principal, make_admin_tenant, override_admin_context

SECRET = "test-s5-invariant-secret"
ALG = "HS256"


# --- shared schema + fixtures ---


class _Base(DeclarativeBase):
    pass


class Account(_Base):
    __tablename__ = "s5_accounts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String, nullable=False)
    hashed_password = Column(String, nullable=True)
    api_secret = Column(String, nullable=True)
    is_system = Column(Boolean, nullable=False, default=False)


class AccountAdmin(ModelAdmin):
    model = Account
    list_display = ["id", "email"]
    readonly_fields = ["id"]
    protected_fields = ["api_secret"]


class _StubAccount:
    __table__ = Account.__table__

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


@pytest.fixture
def app_with_user(tmp_path):
    """A minimal admin app with one registered resource and one inactive-able user."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 's5.db'}"
    application = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key=SECRET,
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        ),
        register=lambda reg: reg.register(AccountAdmin),
    )
    runtime = application.state.asterion

    async def _setup():
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
            await conn.run_sync(Account.metadata.create_all)
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                u = User(
                    email="alice@example.com",
                    hashed_password=hash_password("hunter2-strong"),
                    is_active=True,
                    is_superadmin=False,
                    token_version=0,
                )
                session.add(u)
            await session.refresh(u)
            return u

    user = asyncio.run(_setup())
    yield application, runtime, user
    asyncio.run(runtime.db.dispose())


def _grant(app, email: str, keys: set[str]) -> None:
    override_admin_context(
        app,
        principal=make_admin_principal(email=email),
        tenant=make_admin_tenant("acme"),
        permissions=frozenset(keys),
    )


# --- S5.1: hidden fields never serialized ---


def test_s5_hidden_fields_never_serialized():
    """Per-admin ``protected_fields`` must never appear in serializer output."""
    obj = _StubAccount(
        id=1,
        email="a@b.c",
        hashed_password=None,
        api_secret="topsecret",
        is_system=False,
    )
    out = serialize_record(obj, AccountAdmin())
    assert "api_secret" not in out, "protected_fields leaked into serializer output"


# --- S5.2: hashed_password never serialized ---


def test_s5_hashed_password_never_serialized():
    """``hashed_password`` is in ``GLOBALLY_PROTECTED`` and must never leak."""
    obj = _StubAccount(
        id=1,
        email="a@b.c",
        hashed_password="$2b$leak",
        api_secret=None,
        is_system=False,
    )
    out = serialize_record(obj, AccountAdmin())
    assert "hashed_password" not in out, "GLOBALLY_PROTECTED field leaked"


# --- S5.3: readonly fields not writable ---


def test_s5_readonly_fields_not_writable():
    """Write payloads naming a read-only field must be rejected with 422."""
    schema = build_model_schema(AccountAdmin())
    with pytest.raises(HTTPException) as exc:
        clean_write_payload({"id": 99, "email": "x@y.com"}, schema, partial=False)
    assert exc.value.status_code == 422


# --- S5.4: inactive user rejected ---


def test_s5_inactive_user_rejected(app_with_user):
    """A valid token for a deactivated user must fail authentication."""
    app, runtime, user = app_with_user
    token = create_access_token(
        user.id,
        secret_key=SECRET,
        algorithm=ALG,
        expires_minutes=5,
        token_version=user.token_version,
    )

    async def _deactivate():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                fresh = await session.get(User, user.id)
                fresh.is_active = False

    asyncio.run(_deactivate())

    # Hit any authenticated route — the contract endpoint is enough.
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/api/v1/admin/_contract", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code in (401, 403), (
        f"Inactive user must not pass authentication, got {resp.status_code}"
    )


# --- S5.5: token_version mismatch rejected ---


def test_s5_token_version_mismatch_rejected(app_with_user):
    """Bumping User.token_version invalidates every previously-issued JWT."""
    app, runtime, user = app_with_user
    token = create_access_token(
        user.id,
        secret_key=SECRET,
        algorithm=ALG,
        expires_minutes=5,
        token_version=user.token_version,
    )

    async def _bump():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                fresh = await session.get(User, user.id)
                fresh.token_version += 1

    asyncio.run(_bump())

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/api/v1/admin/_contract", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401, (
        f"Stale tkv claim must be rejected with 401, got {resp.status_code}"
    )


# --- S5.6: impersonation token rejected by require_superadmin ---


def test_s5_impersonation_token_rejected_by_require_superadmin(app_with_user):
    """Even a superadmin's impersonation token must not pass require_superadmin."""
    app, runtime, user = app_with_user

    async def _make_super():
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                fresh = await session.get(User, user.id)
                fresh.is_superadmin = True

    asyncio.run(_make_super())

    imp_token = create_impersonation_token(
        user.id,
        impersonated_by_user_id=user.id,
        tenant_id=None,
        secret_key=SECRET,
        algorithm=ALG,
        token_version=user.token_version,
    )
    # /api/v1/root/users is gated by require_superadmin.
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/api/v1/root/users", headers={"Authorization": f"Bearer {imp_token}"})
    assert resp.status_code == 403, (
        f"Impersonation token must be rejected on root routes, got {resp.status_code}"
    )


# --- S5.7: unknown permission denied ---


def test_s5_unknown_permission_denied():
    """``assert_permission`` raises 403 when the granted set does not match."""
    granted = {"admin.foo.list"}
    required = permission_key("foo", "create")
    assert has_permission(granted, required) is False
    with pytest.raises(HTTPException) as exc:
        assert_permission(granted, required)
    assert exc.value.status_code == 403


def test_s5_wildcard_permission_grants_required():
    """Bonus check: ``admin.*`` is the only wildcard form that grants ``admin.foo.create``.
    Guards against regressions in the wildcard matcher."""
    assert has_permission({"admin.*"}, "admin.foo.create") is True
    assert has_permission({"admin.foo.*"}, "admin.foo.create") is True
    # A wildcard in the middle does NOT match — only the trailing form is supported.
    assert has_permission({"admin.*.create"}, "admin.foo.create") is False


# --- E2E sanity: full deny path through the CRUD router ---


def test_s5_unknown_permission_denied_at_crud_layer(app_with_user):
    """Roundtrip via the real CRUD router — proves the gate is wired, not
    just that the utility function works in isolation."""
    app, _runtime, _user = app_with_user
    _grant(app, "alice@example.com", {"admin.s5_accounts.list"})  # list ok, create not
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post("/api/v1/admin/s5_accounts", json={"email": "x@y.com"})
    assert resp.status_code == 403
