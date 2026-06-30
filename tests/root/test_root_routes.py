"""HTTP integration tests for GET /api/v1/root/{users,tenants}."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion import CoreAdminConfig, create_admin
from asterion.auth.password import hash_password
from asterion.auth.tokens import create_access_token, create_impersonation_token
from asterion.models.base import GlobalModel
from asterion.models.tenant import Tenant
from asterion.models.user import User

SECRET = "test-root-secret"
ALG = "HS256"


@pytest.fixture
def app_state(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'root.db'}"
    application = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key=SECRET,
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        )
    )
    runtime = application.state.asterion

    state: dict = {"superadmin": None, "user": None, "tenants": []}

    async def _setup():
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                superadmin = User(
                    email="root@example.com",
                    hashed_password=hash_password("hunter2-strong"),
                    is_active=True,
                    is_superadmin=True,
                )
                normal = User(
                    email="alice@example.com",
                    hashed_password=hash_password("hunter2-strong"),
                    full_name="Alice",
                    is_active=True,
                )
                session.add(superadmin)
                session.add(normal)
                for slug in ("acme", "beta", "gamma"):
                    session.add(
                        Tenant(
                            slug=slug,
                            name=slug.title(),
                            schema_name=f"tenant_{slug}",
                            is_active=True,
                        )
                    )
            await session.refresh(superadmin)
            await session.refresh(normal)
            state["superadmin"] = superadmin
            state["user"] = normal

    asyncio.run(_setup())

    yield application, state

    asyncio.run(runtime.db.dispose())


def _client(app):
    return TestClient(app, raise_server_exceptions=False)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _superadmin_token(state) -> str:
    su = state["superadmin"]
    return create_access_token(
        su.id,
        secret_key=SECRET,
        algorithm=ALG,
        expires_minutes=10,
        token_version=su.token_version,
    )


def _user_token(state) -> str:
    u = state["user"]
    return create_access_token(
        u.id,
        secret_key=SECRET,
        algorithm=ALG,
        expires_minutes=10,
        token_version=u.token_version,
    )


def _impersonation_token(state) -> str:
    return create_impersonation_token(
        state["user"].id,
        impersonated_by_user_id=state["superadmin"].id,
        tenant_id=None,
        secret_key=SECRET,
        algorithm=ALG,
        expires_minutes=10,
        token_version=state["user"].token_version,
    )


# --- /users ---


def test_users_list_requires_authentication(app_state):
    app, _ = app_state
    resp = _client(app).get("/api/v1/root/users")
    assert resp.status_code == 401


def test_users_list_rejects_normal_user(app_state):
    app, state = app_state
    resp = _client(app).get("/api/v1/root/users", headers=_bearer(_user_token(state)))
    assert resp.status_code == 403


def test_users_list_rejects_impersonation_token(app_state):
    app, state = app_state
    resp = _client(app).get(
        "/api/v1/root/users",
        headers=_bearer(_impersonation_token(state)),
    )
    assert resp.status_code == 403


def test_users_list_superadmin_succeeds(app_state):
    app, state = app_state
    resp = _client(app).get("/api/v1/root/users", headers=_bearer(_superadmin_token(state)))
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"items", "total", "limit", "offset"}
    assert body["total"] == 2  # superadmin + alice
    emails = {item["email"] for item in body["items"]}
    assert {"root@example.com", "alice@example.com"} == emails


def test_users_list_never_leaks_hashed_password(app_state):
    app, state = app_state
    resp = _client(app).get("/api/v1/root/users", headers=_bearer(_superadmin_token(state)))
    text = resp.text
    assert "hashed_password" not in text
    assert "$2b$" not in text  # bcrypt prefix


def test_users_list_search_filters(app_state):
    app, state = app_state
    resp = _client(app).get(
        "/api/v1/root/users?search=alice",
        headers=_bearer(_superadmin_token(state)),
    )
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["email"] == "alice@example.com"


def test_users_list_pagination_bounds(app_state):
    app, state = app_state
    resp = _client(app).get(
        "/api/v1/root/users?limit=99999&offset=-5",
        headers=_bearer(_superadmin_token(state)),
    )
    body = resp.json()
    assert body["limit"] <= 500
    assert body["offset"] == 0


def test_user_detail_superadmin_succeeds(app_state):
    app, state = app_state
    resp = _client(app).get(
        f"/api/v1/root/users/{state['user'].id}",
        headers=_bearer(_superadmin_token(state)),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "alice@example.com"
    assert body["full_name"] == "Alice"
    assert "hashed_password" not in body


def test_user_detail_unknown_returns_404(app_state):
    app, state = app_state
    resp = _client(app).get(
        f"/api/v1/root/users/{uuid.uuid4()}",
        headers=_bearer(_superadmin_token(state)),
    )
    assert resp.status_code == 404


def test_user_detail_invalid_uuid_returns_422(app_state):
    app, state = app_state
    resp = _client(app).get(
        "/api/v1/root/users/not-a-uuid",
        headers=_bearer(_superadmin_token(state)),
    )
    assert resp.status_code == 422


def test_user_detail_rejects_normal_user(app_state):
    app, state = app_state
    resp = _client(app).get(
        f"/api/v1/root/users/{state['user'].id}",
        headers=_bearer(_user_token(state)),
    )
    assert resp.status_code == 403


# --- data-subject rights (G8) ---


def test_subject_export_rejects_normal_user(app_state):
    app, state = app_state
    uid = state["user"].id
    resp = _client(app).get(f"/api/v1/root/users/{uid}/export", headers=_bearer(_user_token(state)))
    assert resp.status_code == 403


def test_subject_export_unknown_user_404(app_state):
    app, state = app_state
    resp = _client(app).get(
        f"/api/v1/root/users/{uuid.uuid4()}/export",
        headers=_bearer(_superadmin_token(state)),
    )
    assert resp.status_code == 404


def test_subject_export_returns_bundle_and_logs_dsar(app_state):
    app, state = app_state
    uid = state["user"].id
    resp = _client(app).get(
        f"/api/v1/root/users/{uid}/export", headers=_bearer(_superadmin_token(state))
    )
    assert resp.status_code == 200, resp.text
    bundle = resp.json()
    assert bundle["subject"]["email"] == "alice@example.com"
    assert "hashed_password" not in bundle["subject"]

    # The export auto-logged a completed 'access' DSAR row.
    dsar = (
        _client(app)
        .get(f"/api/v1/root/users/{uid}/dsar", headers=_bearer(_superadmin_token(state)))
        .json()
    )
    assert any(r["request_type"] == "access" and r["status"] == "completed" for r in dsar)


def test_create_dsar_request(app_state):
    app, state = app_state
    uid = state["user"].id
    resp = _client(app).post(
        f"/api/v1/root/users/{uid}/dsar",
        json={"request_type": "erasure", "note": "ticket-7"},
        headers=_bearer(_superadmin_token(state)),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["request_type"] == "erasure"


def test_create_dsar_rejects_bad_type(app_state):
    app, state = app_state
    uid = state["user"].id
    resp = _client(app).post(
        f"/api/v1/root/users/{uid}/dsar",
        json={"request_type": "bogus"},
        headers=_bearer(_superadmin_token(state)),
    )
    # FastAPI rejects the Literal at validation time → 422.
    assert resp.status_code == 422


def test_create_dsar_unknown_user_404(app_state):
    app, state = app_state
    resp = _client(app).post(
        f"/api/v1/root/users/{uuid.uuid4()}/dsar",
        json={"request_type": "access"},
        headers=_bearer(_superadmin_token(state)),
    )
    assert resp.status_code == 404


# --- /tenants ---


def test_tenants_list_requires_authentication(app_state):
    app, _ = app_state
    resp = _client(app).get("/api/v1/root/tenants")
    assert resp.status_code == 401


def test_tenants_list_rejects_normal_user(app_state):
    app, state = app_state
    resp = _client(app).get("/api/v1/root/tenants", headers=_bearer(_user_token(state)))
    assert resp.status_code == 403


def test_tenants_list_rejects_impersonation_token(app_state):
    app, state = app_state
    resp = _client(app).get(
        "/api/v1/root/tenants",
        headers=_bearer(_impersonation_token(state)),
    )
    assert resp.status_code == 403


def test_tenants_list_superadmin_succeeds(app_state):
    app, state = app_state
    resp = _client(app).get("/api/v1/root/tenants", headers=_bearer(_superadmin_token(state)))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    slugs = sorted(item["slug"] for item in body["items"])
    assert slugs == ["acme", "beta", "gamma"]


def test_tenants_list_search_filters(app_state):
    app, state = app_state
    resp = _client(app).get(
        "/api/v1/root/tenants?search=beta",
        headers=_bearer(_superadmin_token(state)),
    )
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["slug"] == "beta"


def test_tenant_detail_succeeds(app_state):
    app, state = app_state
    list_body = (
        _client(app).get("/api/v1/root/tenants", headers=_bearer(_superadmin_token(state))).json()
    )
    first_id = list_body["items"][0]["id"]
    resp = _client(app).get(
        f"/api/v1/root/tenants/{first_id}",
        headers=_bearer(_superadmin_token(state)),
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == first_id


def test_tenant_detail_unknown_returns_404(app_state):
    app, state = app_state
    resp = _client(app).get(
        f"/api/v1/root/tenants/{uuid.uuid4()}",
        headers=_bearer(_superadmin_token(state)),
    )
    assert resp.status_code == 404


# --- isolation from tenant CRUD ---


def test_root_routes_never_register_global_models_as_tenant_admins(app_state):
    """Plan §Phase 9: root routes must be separate from tenant-local CRUD.
    The /api/v1/admin/users path must not resolve a tenant CRUD admin
    when the registry has no User admin registered."""
    app, state = app_state
    resp = _client(app).get("/api/v1/admin/users", headers=_bearer(_superadmin_token(state)))
    # 404 (not registered as tenant admin) — root models are NOT exposed
    # via the tenant-local CRUD router.
    assert resp.status_code in (401, 403, 404)


# --- POST /tenants/{id}/access (superadmin tenant entry) ---


def _first_tenant(app, state):
    listing = (
        _client(app).get("/api/v1/root/tenants", headers=_bearer(_superadmin_token(state))).json()
    )
    return listing["items"][0]


def test_access_tenant_records_global_audit(app_state):
    app, state = app_state
    tenant = _first_tenant(app, state)
    resp = _client(app).post(
        f"/api/v1/root/tenants/{tenant['id']}/access",
        headers=_bearer(_superadmin_token(state)),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["slug"] == tenant["slug"]

    runtime = app.state.asterion

    async def _rows():
        from sqlalchemy import select

        from asterion.models.audit_log import AuditLog

        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            return (
                (await session.execute(select(AuditLog).where(AuditLog.action == "tenant_access")))
                .scalars()
                .all()
            )

    rows = asyncio.run(_rows())
    assert len(rows) == 1
    assert str(rows[0].tenant_id) == tenant["id"]
    assert rows[0].actor_label == "root@example.com"


# --- POST /tenants/{id}/offboard (G6) ---


def test_offboard_rejects_normal_user(app_state):
    app, state = app_state
    tenant = _first_tenant(app, state)
    resp = _client(app).post(
        f"/api/v1/root/tenants/{tenant['id']}/offboard",
        json={"mode": "archive"},
        headers=_bearer(_user_token(state)),
    )
    assert resp.status_code == 403


def test_offboard_unknown_tenant_returns_404(app_state):
    app, state = app_state
    resp = _client(app).post(
        f"/api/v1/root/tenants/{uuid.uuid4()}/offboard",
        json={"mode": "archive"},
        headers=_bearer(_superadmin_token(state)),
    )
    assert resp.status_code == 404


def test_offboard_archive_marks_tenant_inactive(app_state):
    app, state = app_state
    tenant = _first_tenant(app, state)
    resp = _client(app).post(
        f"/api/v1/root/tenants/{tenant['id']}/offboard",
        json={"mode": "archive"},
        headers=_bearer(_superadmin_token(state)),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "archive"
    assert body["schema_dropped"] is False  # SQLite
    assert "memberships" in body["public_rows_deleted"]

    # The tenant detail now reports it inactive (tombstone kept).
    detail = _client(app).get(
        f"/api/v1/root/tenants/{tenant['id']}",
        headers=_bearer(_superadmin_token(state)),
    )
    assert detail.status_code == 200
    assert detail.json()["is_active"] is False


def test_offboard_drop_deletes_tenant(app_state):
    app, state = app_state
    tenant = _first_tenant(app, state)
    resp = _client(app).post(
        f"/api/v1/root/tenants/{tenant['id']}/offboard",
        json={"mode": "drop"},
        headers=_bearer(_superadmin_token(state)),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["mode"] == "drop"

    # The tenant row is gone → detail 404s.
    detail = _client(app).get(
        f"/api/v1/root/tenants/{tenant['id']}",
        headers=_bearer(_superadmin_token(state)),
    )
    assert detail.status_code == 404


def test_access_tenant_requires_superadmin(app_state):
    app, state = app_state
    tenant = _first_tenant(app, state)
    resp = _client(app).post(
        f"/api/v1/root/tenants/{tenant['id']}/access",
        headers=_bearer(_user_token(state)),
    )
    assert resp.status_code == 403


def test_access_unknown_tenant_404(app_state):
    app, state = app_state
    resp = _client(app).post(
        f"/api/v1/root/tenants/{uuid.uuid4()}/access",
        headers=_bearer(_superadmin_token(state)),
    )
    assert resp.status_code == 404


# --- impersonation auto-enters the target's single tenant ---


def test_impersonate_auto_enters_single_tenant(app_state):
    app, state = app_state
    tenant = next(t for t in _first_tenant_list(app, state) if t["slug"] == "acme")

    runtime = app.state.asterion

    async def _seed_membership():
        from asterion.models.tenant_membership import TenantMembership

        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                session.add(
                    TenantMembership(
                        user_id=state["user"].id,
                        tenant_id=uuid.UUID(tenant["id"]),
                        is_active=True,
                    )
                )

    asyncio.run(_seed_membership())

    resp = _client(app).post(
        "/api/v1/root/impersonate",
        headers=_bearer(_superadmin_token(state)),
        json={"target_user_id": str(state["user"].id), "reason": "tenant auto-enter test"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tenant_slug"] == "acme"
    assert body["tenant_id"] == tenant["id"]


def _first_tenant_list(app, state):
    return (
        _client(app)
        .get("/api/v1/root/tenants", headers=_bearer(_superadmin_token(state)))
        .json()["items"]
    )
