"""Permission-matrix router (Roadmap 5.2).

Covers GET + PUT semantics:

* GET returns roles + catalog + assignments with stable sort orders;
* PUT diffs and applies assignment changes per role;
* PUT rejects unknown roles, unknown permission keys, and system
  roles up front so partial application can't leave operators
  guessing;
* both endpoints enforce the permission keys they advertise.
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
from asterion.db.dependencies import get_async_session
from asterion.models.base import GLOBAL_METADATA, TenantBase
from asterion.models.permission_catalog import PermissionCatalog
from asterion.models.tenant_rbac import TenantRole, TenantRolePermission
from asterion.providers.base import AdminPrincipal

SECRET = "x" * 64


def _ctx_factory(perms: set[str]):
    def _make() -> AdminContext:
        return AdminContext(
            request=None,
            principal=AdminPrincipal(id="alice", email="alice@example.com"),
            tenant=None,
            permissions=frozenset(perms),
        )

    return _make


@pytest_asyncio.fixture
async def matrix_app(tmp_path):
    """App + seeded RBAC fixture.

    Seeds two non-system roles, one system role, a permission catalog
    with a few keys spanning two categories, and one existing
    assignment so PUT's diff path is exercised by adding AND removing.
    """
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
    engine = app.state.asterion.db.engine
    async with engine.begin() as conn:
        await conn.run_sync(GLOBAL_METADATA.create_all)
        # TenantRole / TenantRolePermission live in the tenant
        # metadata — without enable_multi_tenant the
        # schema_translate_map collapses to None on SQLite, so the
        # tenant tables sit happily alongside the global ones.
        await conn.run_sync(TenantBase.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_session():
        async with factory() as session:
            async with session.begin():
                yield session

    app.dependency_overrides[get_async_session] = _override_session
    # Permissive ctx — individual tests narrow this down to test the
    # permission gate by overriding again.
    app.dependency_overrides[build_admin_context] = _ctx_factory({"admin.*"})
    app.dependency_overrides[require_admin_context] = _ctx_factory({"admin.*"})

    role_ids: dict[str, uuid.UUID] = {}

    async def _seed():
        async with factory() as session:
            async with session.begin():
                editor = TenantRole(name="editor", is_system=False)
                viewer = TenantRole(name="viewer", is_system=False)
                owner = TenantRole(name="owner", is_system=True)
                session.add_all([editor, viewer, owner])
            await session.refresh(editor)
            await session.refresh(viewer)
            await session.refresh(owner)
            role_ids["editor"] = editor.id
            role_ids["viewer"] = viewer.id
            role_ids["owner"] = owner.id

        # Fresh session for catalog + assignment — the prior one
        # auto-begins a new txn after refresh and a second .begin()
        # collides with it.
        async with factory() as session:
            async with session.begin():
                for key, category in [
                    ("admin.posts.list", "posts"),
                    ("admin.posts.create", "posts"),
                    ("admin.posts.delete", "posts"),
                    ("admin.users.list", "users"),
                    ("admin.users.read", "users"),
                ]:
                    session.add(PermissionCatalog(key=key, category=category))
                # One pre-existing assignment for editor so PUT's
                # add+remove diff path is exercised.
                session.add(
                    TenantRolePermission(
                        role_id=role_ids["editor"],
                        permission_key="admin.posts.list",
                    )
                )

    await _seed()
    yield TestClient(app), app, role_ids
    await engine.dispose()


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------


def test_get_returns_roles_permissions_and_assignments(matrix_app):
    client, _, role_ids = matrix_app
    resp = client.get("/api/v1/admin/_permission_matrix")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    role_names = [r["name"] for r in body["roles"]]
    assert role_names == sorted(role_names)
    # Both non-system + system roles are listed so the UI can render
    # them grey-out; system flag is on the wire.
    assert {"editor", "viewer", "owner"} <= set(role_names)
    owner = next(r for r in body["roles"] if r["name"] == "owner")
    assert owner["is_system"] is True

    # Permissions: sorted by (category, key) — pin so the UI can
    # group reliably.
    perm_keys = [p["key"] for p in body["permissions"]]
    assert perm_keys == sorted(perm_keys, key=lambda k: (k.split(".")[1], k))

    assignments = body["assignments"]
    assert assignments[str(role_ids["editor"])] == ["admin.posts.list"]
    # Roles with no assignments still get an empty list — the UI
    # iterates over the dict keys, so a missing entry would be a
    # rendering bug.
    assert assignments[str(role_ids["viewer"])] == []


def test_get_requires_admin_tenant_roles_list(matrix_app):
    """The endpoint advertises ``admin.tenant_roles.list``. A caller
    with only some unrelated permission must get 403."""
    client, app, _ = matrix_app
    app.dependency_overrides[build_admin_context] = _ctx_factory({"admin.posts.list"})
    app.dependency_overrides[require_admin_context] = _ctx_factory({"admin.posts.list"})
    resp = client.get("/api/v1/admin/_permission_matrix")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PUT — happy path
# ---------------------------------------------------------------------------


def test_put_adds_new_assignments(matrix_app):
    client, _, role_ids = matrix_app
    resp = client.put(
        "/api/v1/admin/_permission_matrix",
        json={
            "assignments": {
                str(role_ids["viewer"]): [
                    "admin.posts.list",
                    "admin.users.list",
                ]
            }
        },
    )
    assert resp.status_code == 200, resp.text
    assignments = resp.json()["assignments"]
    assert sorted(assignments[str(role_ids["viewer"])]) == [
        "admin.posts.list",
        "admin.users.list",
    ]


def test_put_removes_revoked_assignments(matrix_app):
    """Empty list = revoke everything. The editor role starts with
    one permission; sending [] must clear it."""
    client, _, role_ids = matrix_app
    resp = client.put(
        "/api/v1/admin/_permission_matrix",
        json={"assignments": {str(role_ids["editor"]): []}},
    )
    assert resp.status_code == 200, resp.text
    assignments = resp.json()["assignments"]
    assert assignments[str(role_ids["editor"])] == []


def test_put_diffs_adds_and_removes_in_one_call(matrix_app):
    """Editor currently has ``admin.posts.list``. Sending
    ``[admin.posts.create]`` must remove list AND add create —
    proves the diff is per-role and idempotent."""
    client, _, role_ids = matrix_app
    resp = client.put(
        "/api/v1/admin/_permission_matrix",
        json={"assignments": {str(role_ids["editor"]): ["admin.posts.create"]}},
    )
    assert resp.status_code == 200, resp.text
    assignments = resp.json()["assignments"]
    assert assignments[str(role_ids["editor"])] == ["admin.posts.create"]


def test_put_only_touches_listed_roles(matrix_app):
    """A PUT for editor MUST NOT change viewer's assignments —
    partial-submit semantics."""
    client, _, role_ids = matrix_app
    # Pre-set viewer to have one perm so we can prove it survives.
    client.put(
        "/api/v1/admin/_permission_matrix",
        json={"assignments": {str(role_ids["viewer"]): ["admin.users.read"]}},
    )
    # Now PUT only editor.
    client.put(
        "/api/v1/admin/_permission_matrix",
        json={"assignments": {str(role_ids["editor"]): ["admin.posts.delete"]}},
    )
    # Viewer survived untouched.
    resp = client.get("/api/v1/admin/_permission_matrix")
    assignments = resp.json()["assignments"]
    assert assignments[str(role_ids["viewer"])] == ["admin.users.read"]
    assert assignments[str(role_ids["editor"])] == ["admin.posts.delete"]


# ---------------------------------------------------------------------------
# PUT — rejection paths
# ---------------------------------------------------------------------------


def test_put_rejects_unknown_role(matrix_app):
    client, _, _ = matrix_app
    resp = client.put(
        "/api/v1/admin/_permission_matrix",
        json={"assignments": {str(uuid.uuid4()): ["admin.posts.list"]}},
    )
    assert resp.status_code == 404


def test_put_rejects_malformed_role_id(matrix_app):
    client, _, _ = matrix_app
    resp = client.put(
        "/api/v1/admin/_permission_matrix",
        json={"assignments": {"not-a-uuid": ["admin.posts.list"]}},
    )
    assert resp.status_code == 400


def test_put_rejects_unknown_permission_key(matrix_app):
    """A permission key that's not in PermissionCatalog must be
    rejected with a clear error — silently dropping it would mask
    UI bugs."""
    client, _, role_ids = matrix_app
    resp = client.put(
        "/api/v1/admin/_permission_matrix",
        json={"assignments": {str(role_ids["editor"]): ["admin.posts.list", "admin.bogus.thing"]}},
    )
    assert resp.status_code == 400
    assert "admin.bogus.thing" in resp.json()["error"]["message"]


def test_put_rejects_invalid_permission_key_shape(matrix_app):
    client, _, role_ids = matrix_app
    resp = client.put(
        "/api/v1/admin/_permission_matrix",
        json={"assignments": {str(role_ids["editor"]): ["NOT A VALID KEY"]}},
    )
    assert resp.status_code == 400


def test_put_refuses_to_edit_system_role(matrix_app):
    """System roles (``owner``) are bootstrapped with broad grants;
    editing them via the matrix would silently break tenant
    onboarding. Reject up front."""
    client, _, role_ids = matrix_app
    resp = client.put(
        "/api/v1/admin/_permission_matrix",
        json={"assignments": {str(role_ids["owner"]): ["admin.posts.list"]}},
    )
    assert resp.status_code == 403


def test_put_requires_admin_tenant_role_permissions_update(matrix_app):
    """PUT advertises ``admin.tenant_role_permissions.update`` — a
    caller with only the GET key gets 403."""
    client, app, role_ids = matrix_app
    app.dependency_overrides[build_admin_context] = _ctx_factory({"admin.tenant_roles.list"})
    app.dependency_overrides[require_admin_context] = _ctx_factory({"admin.tenant_roles.list"})
    resp = client.put(
        "/api/v1/admin/_permission_matrix",
        json={"assignments": {str(role_ids["editor"]): ["admin.posts.create"]}},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------------


def test_put_is_atomic_under_validation_failure(matrix_app):
    """A request that touches editor (valid) AND owner (system =
    rejected) must leave editor untouched — the validation gate
    runs before any writes."""
    client, _, role_ids = matrix_app
    resp = client.put(
        "/api/v1/admin/_permission_matrix",
        json={
            "assignments": {
                str(role_ids["editor"]): ["admin.posts.delete"],
                str(role_ids["owner"]): ["admin.posts.list"],
            }
        },
    )
    assert resp.status_code == 403  # owner is system

    # Editor's pre-existing single assignment must still be the only one.
    assignments = client.get("/api/v1/admin/_permission_matrix").json()["assignments"]
    assert assignments[str(role_ids["editor"])] == ["admin.posts.list"]
