"""Tenant member-management router.

Covers the onboarding gap closed in v0.1.3: a tenant operator adds/invites
members, toggles membership active state, assigns tenant roles, and removes
members — all strictly scoped to the caller's own tenant.

Exercises:
* GET lists only the caller's tenant members, with roles;
* POST links an existing global user (idempotent) and invites a brand-new one
  (inactive + passwordless user, invite token issued, notifier called);
* the issued invite token activates the account at the existing
  ``/auth/password-reset/confirm`` endpoint;
* PATCH sets active flag + replaces role set; DELETE removes the membership
  but not the global user;
* every endpoint enforces its advertised permission key;
* cross-tenant membership ids are invisible (404 / absent from list).
"""

from __future__ import annotations

import uuid

import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion import CoreAdminConfig, create_admin
from asterion.admin.context import (
    AdminContext,
    build_admin_context,
    require_admin_context,
)
from asterion.auth.password import hash_password
from asterion.db.dependencies import get_async_session
from asterion.models.base import GLOBAL_METADATA, TenantBase
from asterion.models.tenant_membership import TenantMembership
from asterion.models.tenant_rbac import TenantRole
from asterion.models.user import User
from asterion.providers.base import AdminPrincipal, AdminTenant

SECRET = "x" * 64
TENANT_ID = uuid.uuid4()
OTHER_TENANT_ID = uuid.uuid4()


class _CapturingInviteNotifier:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def send_invite(self, *, email, token, tenant_slug=None, request=None) -> None:
        self.calls.append({"email": email, "token": token, "tenant_slug": tenant_slug})


def _ctx_factory(perms: set[str], *, tenant: AdminTenant | None):
    def _make() -> AdminContext:
        return AdminContext(
            request=None,
            principal=AdminPrincipal(id="alice", email="alice@example.com"),
            tenant=tenant,
            permissions=frozenset(perms),
        )

    return _make


_TENANT = AdminTenant(id=str(TENANT_ID), slug="acme", schema_name="tenant_acme")


@pytest_asyncio.fixture
async def member_app():
    app = create_admin(
        config=CoreAdminConfig(
            secret_key=SECRET,
            database_url="sqlite+aiosqlite:///:memory:",
            environment="development",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        ),
    )
    notifier = _CapturingInviteNotifier()
    app.state.asterion.invite_notifier = notifier

    engine = app.state.asterion.db.engine
    async with engine.begin() as conn:
        await conn.run_sync(GLOBAL_METADATA.create_all)
        await conn.run_sync(TenantBase.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_session():
        async with factory() as session:
            async with session.begin():
                yield session

    app.dependency_overrides[get_async_session] = _override_session
    app.dependency_overrides[build_admin_context] = _ctx_factory({"admin.*"}, tenant=_TENANT)
    app.dependency_overrides[require_admin_context] = _ctx_factory({"admin.*"}, tenant=_TENANT)

    ids: dict[str, uuid.UUID] = {}

    async with factory() as session:
        async with session.begin():
            # Two tenant-local roles.
            admin_role = TenantRole(name="admin", is_system=False)
            viewer_role = TenantRole(name="viewer", is_system=False)
            session.add_all([admin_role, viewer_role])
            # An existing global user with a membership in THIS tenant.
            existing = User(
                email="bob@example.com",
                hashed_password=hash_password("pw-strong-1"),
                is_active=True,
            )
            # A user that belongs only to ANOTHER tenant — must stay invisible.
            other = User(
                email="carol@example.com",
                hashed_password=hash_password("pw-strong-2"),
                is_active=True,
            )
            session.add_all([existing, other])
            await session.flush()
            bob_membership = TenantMembership(
                user_id=existing.id, tenant_id=TENANT_ID, is_active=True
            )
            carol_membership = TenantMembership(
                user_id=other.id, tenant_id=OTHER_TENANT_ID, is_active=True
            )
            session.add_all([bob_membership, carol_membership])
            await session.flush()
            ids["admin_role"] = admin_role.id
            ids["viewer_role"] = viewer_role.id
            ids["bob_user"] = existing.id
            ids["bob_membership"] = bob_membership.id
            ids["carol_membership"] = carol_membership.id

    yield TestClient(app), app, ids, notifier, factory
    await engine.dispose()


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------


def test_list_returns_only_this_tenant(member_app):
    client, _, ids, _, _ = member_app
    resp = client.get("/api/v1/admin/_members")
    assert resp.status_code == 200, resp.text
    members = resp.json()["members"]
    emails = {m["email"] for m in members}
    assert emails == {"bob@example.com"}  # carol is in another tenant
    assert members[0]["membership_id"] == str(ids["bob_membership"])
    assert members[0]["roles"] == []


def test_list_requires_permission(member_app):
    client, app, _, _, _ = member_app
    app.dependency_overrides[require_admin_context] = _ctx_factory(
        {"admin.posts.list"}, tenant=_TENANT
    )
    resp = client.get("/api/v1/admin/_members")
    assert resp.status_code == 403


def test_no_tenant_context_is_400(member_app):
    client, app, _, _, _ = member_app
    app.dependency_overrides[require_admin_context] = _ctx_factory({"admin.*"}, tenant=None)
    resp = client.get("/api/v1/admin/_members")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST — link existing
# ---------------------------------------------------------------------------


def test_add_existing_user_is_idempotent(member_app):
    client, _, ids, notifier, _ = member_app
    resp = client.post(
        "/api/v1/admin/_members",
        json={"email": "bob@example.com", "role_ids": [str(ids["admin_role"])]},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["invited"] is False
    assert body["member"]["membership_id"] == str(ids["bob_membership"])
    assert [r["name"] for r in body["member"]["roles"]] == ["admin"]
    # No invite for an existing user.
    assert notifier.calls == []


def test_add_existing_user_normalizes_email(member_app):
    client, _, ids, _, _ = member_app
    resp = client.post("/api/v1/admin/_members", json={"email": "BOB@example.com"})
    assert resp.status_code == 201, resp.text
    # Same membership, not a new user.
    assert resp.json()["member"]["membership_id"] == str(ids["bob_membership"])


# ---------------------------------------------------------------------------
# POST — invite new
# ---------------------------------------------------------------------------


def test_invite_new_user_creates_inactive_and_issues_token(member_app):
    client, _, ids, notifier, factory = member_app
    resp = client.post(
        "/api/v1/admin/_members",
        json={
            "email": "dave@example.com",
            "full_name": "Dave",
            "role_ids": [str(ids["viewer_role"])],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["invited"] is True
    assert body["member"]["email"] == "dave@example.com"
    assert body["member"]["user_is_active"] is False
    assert [r["name"] for r in body["member"]["roles"]] == ["viewer"]

    # Notifier received the raw token + tenant slug.
    assert len(notifier.calls) == 1
    assert notifier.calls[0]["email"] == "dave@example.com"
    assert notifier.calls[0]["tenant_slug"] == "acme"
    assert notifier.calls[0]["token"]


def test_invite_token_activates_account_on_confirm(member_app):
    """The invite token completes onboarding at the existing
    password-reset/confirm endpoint, which activates the inactive user.

    Proven end-to-end: before confirm the invited user can't log in
    (inactive); after confirm the new password logs in (active)."""
    client, _, _, notifier, _ = member_app
    client.post("/api/v1/admin/_members", json={"email": "erin@example.com"})
    token = notifier.calls[-1]["token"]

    # Inactive + passwordless before acceptance → login must fail.
    pre = client.post(
        "/api/v1/auth/login",
        json={"email": "erin@example.com", "password": "brand-new-pw-99"},
    )
    assert pre.status_code == 401, pre.text

    resp = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "brand-new-pw-99"},
    )
    assert resp.status_code == 200, resp.text

    # Now active with the chosen password → login succeeds.
    post = client.post(
        "/api/v1/auth/login",
        json={"email": "erin@example.com", "password": "brand-new-pw-99"},
    )
    assert post.status_code == 200, post.text
    assert post.json().get("access_token")


# ---------------------------------------------------------------------------
# POST — validation + permission
# ---------------------------------------------------------------------------


def test_add_unknown_role_is_404(member_app):
    client, _, _, _, _ = member_app
    resp = client.post(
        "/api/v1/admin/_members",
        json={"email": "frank@example.com", "role_ids": [str(uuid.uuid4())]},
    )
    assert resp.status_code == 404


def test_add_requires_create_permission(member_app):
    client, app, _, _, _ = member_app
    app.dependency_overrides[require_admin_context] = _ctx_factory(
        {"admin.tenant_members.list"}, tenant=_TENANT
    )
    resp = client.post("/api/v1/admin/_members", json={"email": "x@example.com"})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PATCH
# ---------------------------------------------------------------------------


def test_patch_sets_active_and_roles(member_app):
    client, _, ids, _, _ = member_app
    resp = client.patch(
        f"/api/v1/admin/_members/{ids['bob_membership']}",
        json={"is_active": False, "role_ids": [str(ids["admin_role"]), str(ids["viewer_role"])]},
    )
    assert resp.status_code == 200, resp.text
    member = resp.json()["member"]
    assert member["is_active"] is False
    assert sorted(r["name"] for r in member["roles"]) == ["admin", "viewer"]


def test_patch_replaces_role_set(member_app):
    client, _, ids, _, _ = member_app
    # First give two roles, then PATCH down to one — proves replace semantics.
    client.patch(
        f"/api/v1/admin/_members/{ids['bob_membership']}",
        json={"role_ids": [str(ids["admin_role"]), str(ids["viewer_role"])]},
    )
    resp = client.patch(
        f"/api/v1/admin/_members/{ids['bob_membership']}",
        json={"role_ids": [str(ids["viewer_role"])]},
    )
    assert [r["name"] for r in resp.json()["member"]["roles"]] == ["viewer"]


def test_patch_cross_tenant_membership_is_404(member_app):
    client, _, ids, _, _ = member_app
    resp = client.patch(
        f"/api/v1/admin/_members/{ids['carol_membership']}",
        json={"is_active": False},
    )
    assert resp.status_code == 404


def test_patch_requires_update_permission(member_app):
    client, app, ids, _, _ = member_app
    app.dependency_overrides[require_admin_context] = _ctx_factory(
        {"admin.tenant_members.list"}, tenant=_TENANT
    )
    resp = client.patch(
        f"/api/v1/admin/_members/{ids['bob_membership']}", json={"is_active": False}
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


def test_delete_removes_membership_not_user(member_app):
    client, _, ids, notifier, _ = member_app
    resp = client.delete(f"/api/v1/admin/_members/{ids['bob_membership']}")
    assert resp.status_code == 200, resp.text

    # Membership gone from the list…
    members = client.get("/api/v1/admin/_members").json()["members"]
    assert members == []

    # …but the global user survives: re-adding the same email links the
    # EXISTING account (invited=False, no new invite token) rather than
    # creating a fresh one.
    again = client.post("/api/v1/admin/_members", json={"email": "bob@example.com"})
    assert again.status_code == 201, again.text
    assert again.json()["invited"] is False
    assert notifier.calls == []


def test_delete_cross_tenant_is_404(member_app):
    client, _, ids, _, _ = member_app
    resp = client.delete(f"/api/v1/admin/_members/{ids['carol_membership']}")
    assert resp.status_code == 404


def test_delete_requires_delete_permission(member_app):
    client, app, ids, _, _ = member_app
    app.dependency_overrides[require_admin_context] = _ctx_factory(
        {"admin.tenant_members.update"}, tenant=_TENANT
    )
    resp = client.delete(f"/api/v1/admin/_members/{ids['bob_membership']}")
    assert resp.status_code == 403
