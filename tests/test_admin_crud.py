"""
Phase 4 — Admin CRUD tests.
Covers: registry, list/detail/create/update/delete, filtering,
search, ordering, pagination, protected-field absence, readonly
rejection, tenant scoping, and Phase 1-3 regression.
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from coreAdmin_api.models.user import User
from coreAdmin_api.models.role import Role
from coreAdmin_api.models.tenant import Tenant
from coreAdmin_api.auth import create_access_token, hash_password


def auth(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


# ---------------------------------------------------------------------------
# Registry metadata
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_registered_models(client: AsyncClient, superadmin: User):
    resp = await client.get("/api/v1/admin", headers=auth(superadmin))
    assert resp.status_code == 200
    models = resp.json()["models"]
    model_names = [m["model"] for m in models]
    assert "users" in model_names
    assert "roles" in model_names


@pytest.mark.asyncio
async def test_registry_metadata_no_protected_fields(client: AsyncClient, superadmin: User):
    resp = await client.get("/api/v1/admin", headers=auth(superadmin))
    raw = str(resp.json())
    for protected in ["hashed_password", "password", "pin_hash"]:
        assert protected not in raw, f"Protected field '{protected}' leaked in registry metadata"


@pytest.mark.asyncio
async def test_unknown_model_returns_404(client: AsyncClient, superadmin: User):
    resp = await client.get("/api/v1/admin/nonexistent", headers=auth(superadmin))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# User model — list, detail, update, protected fields
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_admin_list_users(client: AsyncClient, superadmin: User):
    resp = await client.get("/api/v1/admin/users", headers=auth(superadmin))
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_admin_list_response_no_protected_fields(client: AsyncClient, superadmin: User):
    resp = await client.get("/api/v1/admin/users", headers=auth(superadmin))
    raw = str(resp.json())
    assert "hashed_password" not in raw
    assert "password" not in raw


@pytest.mark.asyncio
async def test_admin_detail_no_protected_fields(client: AsyncClient, superadmin: User):
    resp = await client.get(f"/api/v1/admin/users/{superadmin.id}", headers=auth(superadmin))
    assert resp.status_code == 200
    data = resp.json()
    assert "hashed_password" not in data
    assert "password" not in data
    assert "email" in data


@pytest.mark.asyncio
async def test_admin_update_user(client: AsyncClient, superadmin: User, db: AsyncSession):
    user = User(
        email="update-me@example.com",
        hashed_password=hash_password("pw"),
        full_name="Before",
        is_active=True,
        is_superadmin=False,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    resp = await client.patch(
        f"/api/v1/admin/users/{user.id}",
        headers=auth(superadmin),
        json={"full_name": "After"},
    )
    assert resp.status_code == 200
    assert resp.json()["full_name"] == "After"


@pytest.mark.asyncio
async def test_admin_update_rejects_readonly_field(client: AsyncClient, superadmin: User):
    """Sending 'id' (readonly) in PATCH body must return 422."""
    import uuid
    resp = await client.patch(
        f"/api/v1/admin/users/{superadmin.id}",
        headers=auth(superadmin),
        json={"id": str(uuid.uuid4()), "full_name": "x"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_admin_update_rejects_protected_field(client: AsyncClient, superadmin: User):
    """Sending 'hashed_password' must return 422."""
    resp = await client.patch(
        f"/api/v1/admin/users/{superadmin.id}",
        headers=auth(superadmin),
        json={"hashed_password": "new-hash"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_admin_create_rejects_protected_field(client: AsyncClient, superadmin: User):
    resp = await client.post(
        "/api/v1/admin/roles",
        headers=auth(superadmin),
        json={"name": "ok-role", "id": "should-fail"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Role model — full CRUD
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_admin_create_role(client: AsyncClient, superadmin: User):
    resp = await client.post(
        "/api/v1/admin/roles",
        headers=auth(superadmin),
        json={"name": "admin-created"},
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "admin-created"


@pytest.mark.asyncio
async def test_admin_delete_role(client: AsyncClient, superadmin: User):
    created = (
        await client.post(
            "/api/v1/admin/roles",
            headers=auth(superadmin),
            json={"name": "delete-me"},
        )
    ).json()
    resp = await client.delete(
        f"/api/v1/admin/roles/{created['id']}", headers=auth(superadmin)
    )
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_admin_list_pagination(client: AsyncClient, superadmin: User, db: AsyncSession):
    for i in range(5):
        db.add(Role(name=f"pag-role-{i}"))
    await db.commit()

    resp = await client.get(
        "/api/v1/admin/roles?page=1&page_size=2", headers=auth(superadmin)
    )
    data = resp.json()
    assert resp.status_code == 200
    assert len(data["items"]) <= 2
    assert data["pages"] >= 1


# ---------------------------------------------------------------------------
# Search and filtering
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_admin_search(client: AsyncClient, superadmin: User, db: AsyncSession):
    db.add(Role(name="search-unique-xyz"))
    await db.commit()

    resp = await client.get(
        "/api/v1/admin/roles?q=unique-xyz", headers=auth(superadmin)
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert any("unique-xyz" in item["name"] for item in items)


@pytest.mark.asyncio
async def test_admin_filter_by_field(client: AsyncClient, superadmin: User, db: AsyncSession):
    db.add(User(
        email="inactive-filter@x.com",
        hashed_password=hash_password("pw"),
        is_active=False,
        is_superadmin=False,
    ))
    await db.commit()

    resp = await client.get(
        "/api/v1/admin/users?is_active=false", headers=auth(superadmin)
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert all(item["is_active"] is False for item in items)


@pytest.mark.asyncio
async def test_admin_ordering(client: AsyncClient, superadmin: User, db: AsyncSession):
    for name in ["zzz-role", "aaa-role"]:
        db.add(Role(name=name))
    await db.commit()

    resp = await client.get("/api/v1/admin/roles?order_by=name", headers=auth(superadmin))
    assert resp.status_code == 200
    names = [i["name"] for i in resp.json()["items"]]
    assert names == sorted(names)

    resp_desc = await client.get(
        "/api/v1/admin/roles?order_by=-name", headers=auth(superadmin)
    )
    names_desc = [i["name"] for i in resp_desc.json()["items"]]
    assert names_desc == sorted(names_desc, reverse=True)


# ---------------------------------------------------------------------------
# Tenant-scoped model isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tenant_scoped_filter(client: AsyncClient, superadmin: User, db: AsyncSession):
    """tenant_scoped=True filters by tenant_id when MULTI_TENANT=True."""
    from coreAdmin_api.admin import admin_site, ModelAdmin
    from coreAdmin_api.models.user import User
    from unittest.mock import patch

    # Register a scoped UserAdmin temporarily
    class ScopedUserAdmin(ModelAdmin):
        model = User
        list_display = ["email"]
        tenant_scoped = True

    original = admin_site.get("users")
    admin_site.register(ScopedUserAdmin())

    tenant = Tenant(name="Scope Co", slug="scope-co", is_active=True)
    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)

    # User without tenant
    db.add(User(email="no-tenant@x.com", hashed_password=hash_password("pw"), is_active=True, is_superadmin=False))
    # User with tenant
    user_t = User(email="has-tenant@x.com", hashed_password=hash_password("pw"), is_active=True, is_superadmin=False, tenant_id=tenant.id)
    db.add(user_t)
    await db.commit()

    # Simulate tenant in request state
    from coreAdmin_api.middleware import tenant as tenant_mod
    from coreAdmin_api.main import app as main_app
    from coreAdmin_api.database import get_db as real_get_db
    from sqlalchemy.ext.asyncio import async_sessionmaker
    import contextlib

    factory = admin_site  # just a reference to reuse the fixture pattern

    # Override with MULTI_TENANT=True + tenant in state via middleware patch
    with patch("coreAdmin_api.settings.settings.MULTI_TENANT", True):
        with patch("coreAdmin_api.admin.router.settings.MULTI_TENANT", True):
            # Inject tenant into request.state manually via a custom middleware
            from starlette.middleware.base import BaseHTTPMiddleware

            class InjectTenant(BaseHTTPMiddleware):
                async def dispatch(self, req, call_next):
                    req.state.tenant = tenant
                    return await call_next(req)

            from fastapi import FastAPI
            from httpx import AsyncClient, ASGITransport
            from sqlalchemy.ext.asyncio import async_sessionmaker as asm

            test_app = FastAPI()
            test_app.add_middleware(InjectTenant)
            from coreAdmin_api.admin.router import router as admin_router
            test_app.include_router(admin_router)

            db_factory = asm(db.get_bind().engine if hasattr(db.get_bind(), 'engine') else db.bind, expire_on_commit=False)

            async def override():
                yield db

            from coreAdmin_api.dependencies import get_current_user as real_get_current_user
            test_app.dependency_overrides[real_get_db] = override
            test_app.dependency_overrides[require_superadmin] = lambda: superadmin
            test_app.dependency_overrides[real_get_current_user] = lambda: superadmin

            transport = ASGITransport(app=test_app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/v1/admin/users")
            items = resp.json()["items"]
            emails = [i["email"] for i in items]
            assert "has-tenant@x.com" in emails
            assert "no-tenant@x.com" not in emails

    # Restore original admin
    if original:
        admin_site.register(original)


# ---------------------------------------------------------------------------
# Security: admin-only access
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_admin_requires_superadmin(client: AsyncClient, db: AsyncSession):
    user = User(
        email="plain2@x.com",
        hashed_password=hash_password("pw"),
        is_active=True,
        is_superadmin=False,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    resp = await client.get("/api/v1/admin/users", headers=auth(user))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Regression: Phase 1-3 endpoints unaffected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_phase1_login_still_works(client: AsyncClient, superadmin: User):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )
    assert resp.status_code == 200
    assert "hashed_password" not in resp.json()


@pytest.mark.asyncio
async def test_phase2_users_still_works(client: AsyncClient, superadmin: User):
    resp = await client.get("/api/v1/users", headers=auth(superadmin))
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_phase3_tenants_still_works(client: AsyncClient, superadmin: User):
    resp = await client.get("/api/v1/tenants", headers=auth(superadmin))
    assert resp.status_code == 200


# reuse from test_roles for require_role convenience
from coreAdmin_api.dependencies import require_superadmin
