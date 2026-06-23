"""ReadOnlyPolicy (Roadmap 5.1).

Pins the contract: list + detail read paths stay open, but
``can_create`` / ``can_update_object`` / ``can_delete_object`` all
deny so the CRUD router answers 403. End-to-end fixture (create_admin
+ TestClient) verifies the framework actually translates the policy
decision into 403 — not a unit test of the policy in isolation.

A separate test asserts the BuiltinAuditLogAdmin wires this policy
in and that POST/PATCH/DELETE against ``/audit_logs`` are blocked
even when the caller has the admin.* permission.
"""

from __future__ import annotations

import asyncio

import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion import CoreAdminConfig, create_admin
from asterion.admin.context import (
    AdminContext,
    build_admin_context,
    require_admin_context,
)
from asterion.admin.policy import ReadOnlyPolicy
from asterion.builtins.admin import AuditLogAdmin
from asterion.db.dependencies import get_async_session
from asterion.models.audit_log import AuditLog
from asterion.models.base import GLOBAL_METADATA
from asterion.providers.base import AdminPrincipal

SECRET = "x" * 64


def _ctx() -> AdminContext:
    return AdminContext(
        request=None,
        principal=AdminPrincipal(id="alice", email="alice@example.com"),
        tenant=None,
        # "admin.*" so we're testing the policy layer, not key gating
        permissions=frozenset({"admin.*"}),
    )


@pytest_asyncio.fixture
async def audit_app(tmp_path):
    app = create_admin(
        config=CoreAdminConfig(
            secret_key=SECRET,
            database_url="sqlite+aiosqlite:///:memory:",
            environment="development",
            enable_multi_tenant=False,
        ),
    )
    engine = app.state.asterion.db.engine
    async with engine.begin() as conn:
        await conn.run_sync(GLOBAL_METADATA.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_session():
        async with factory() as session:
            async with session.begin():
                yield session

    app.dependency_overrides[get_async_session] = _override_session
    app.dependency_overrides[build_admin_context] = _ctx
    app.dependency_overrides[require_admin_context] = _ctx

    # Seed one row so list + detail have something to return.
    async with factory() as session:
        async with session.begin():
            session.add(
                AuditLog(
                    method="POST",
                    path="/api/v1/admin/posts",
                    status_code=201,
                    resource="posts",
                    record_id="42",
                    action="create",
                    actor_label="alice@example.com",
                    changes={"title": ["", "Hello"]},
                )
            )

    yield TestClient(app)
    await engine.dispose()


# ---------------------------------------------------------------------------
# Policy semantics (unit-level)
# ---------------------------------------------------------------------------


def test_readonly_policy_denies_create():
    policy = ReadOnlyPolicy()
    assert asyncio.run(policy.can_create(_ctx())) is False


def test_readonly_policy_denies_update():
    policy = ReadOnlyPolicy()
    assert asyncio.run(policy.can_update_object(object(), _ctx())) is False


def test_readonly_policy_denies_delete():
    policy = ReadOnlyPolicy()
    assert asyncio.run(policy.can_delete_object(object(), _ctx())) is False


def test_readonly_policy_allows_read():
    """Read paths must stay open — that's the whole point. List
    visibility (``can_view_model``) is independent and stays
    permissive (inherited from the base AdminPolicy)."""
    policy = ReadOnlyPolicy()
    assert asyncio.run(policy.can_view_object(object(), _ctx())) is True
    assert asyncio.run(policy.can_view_model(_ctx())) is True


# ---------------------------------------------------------------------------
# AuditLogAdmin wiring + end-to-end
# ---------------------------------------------------------------------------


def test_audit_log_admin_uses_readonly_policy():
    assert isinstance(AuditLogAdmin.policy, ReadOnlyPolicy)


def test_audit_list_returns_seeded_rows(audit_app):
    resp = audit_app.get("/api/v1/admin/audit_logs/")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] >= 1
    # actor_label is denormalised at write time — the list shows it
    # without an ORM hop. Pin that property here.
    first = body["items"][0]
    assert first["actor_label"] == "alice@example.com"
    assert first["action"] == "create"


def test_audit_detail_returns_full_changes_blob(audit_app):
    rows = audit_app.get("/api/v1/admin/audit_logs/").json()["items"]
    row_id = rows[0]["id"]
    resp = audit_app.get(f"/api/v1/admin/audit_logs/{row_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The ``changes`` blob is the diff — make sure it survives the
    # JSON-adapter roundtrip and reaches the client intact.
    assert body["changes"] == {"title": ["", "Hello"]}


def test_audit_create_is_forbidden(audit_app):
    """ReadOnlyPolicy.can_create denies — POST must 403 even with
    admin.* permissions. This is the security pin for 5.1."""
    resp = audit_app.post(
        "/api/v1/admin/audit_logs/",
        json={
            "method": "POST",
            "path": "/spoof",
            "status_code": 200,
        },
    )
    assert resp.status_code == 403


def test_audit_update_is_forbidden(audit_app):
    row_id = audit_app.get("/api/v1/admin/audit_logs/").json()["items"][0]["id"]
    resp = audit_app.patch(
        f"/api/v1/admin/audit_logs/{row_id}",
        json={"action": "tampered"},
    )
    assert resp.status_code == 403


def test_audit_delete_is_forbidden(audit_app):
    row_id = audit_app.get("/api/v1/admin/audit_logs/").json()["items"][0]["id"]
    resp = audit_app.delete(f"/api/v1/admin/audit_logs/{row_id}")
    assert resp.status_code == 403


def test_read_only_policy_zeroes_contract_capabilities():
    """The contract must report no write capabilities for a ReadOnlyPolicy
    admin even when the caller holds admin.* — so the UI hides New/Edit/Delete
    instead of offering controls the route would 403."""
    from asterion.contract.service import build_model_contract

    contract = build_model_contract(AuditLogAdmin(), permissions=frozenset({"admin.*"}))
    assert contract.capabilities.create is False
    assert contract.capabilities.update is False
    assert contract.capabilities.delete is False
