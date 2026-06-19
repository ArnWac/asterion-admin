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
