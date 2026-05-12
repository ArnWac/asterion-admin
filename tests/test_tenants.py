"""
Phase 3 — Tenant CRUD, middleware, slug validation, and single-tenant regression.

Fast tests only (no Docker). Middleware tests override AsyncSessionLocal to avoid
hitting the production DB during resolution.
"""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from adminfoundry.models.user import User
from adminfoundry.models.tenant import Tenant
from adminfoundry.auth import create_access_token, hash_password


def auth(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


# ---------------------------------------------------------------------------
# Tenant CRUD
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_tenant(client: AsyncClient, superadmin: User):
    resp = await client.post(
        "/api/v1/tenants",
        headers=auth(superadmin),
        json={"name": "Acme Corp", "slug": "acme"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["slug"] == "acme"
    assert data["schema_name"] == "tenant_acme"
    assert data["is_active"] is True


@pytest.mark.asyncio
async def test_create_tenant_duplicate_slug(client: AsyncClient, superadmin: User):
    payload = {"name": "Dupe", "slug": "dupe"}
    await client.post("/api/v1/tenants", headers=auth(superadmin), json=payload)
    resp = await client.post("/api/v1/tenants", headers=auth(superadmin), json=payload)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_create_tenant_invalid_slug(client: AsyncClient, superadmin: User):
    for bad_slug in ["Bad Slug", "UPPER", "has_underscore", "-leading", ""]:
        resp = await client.post(
            "/api/v1/tenants",
            headers=auth(superadmin),
            json={"name": "Bad", "slug": bad_slug},
        )
        assert resp.status_code == 422, f"Expected 422 for slug={bad_slug!r}, got {resp.status_code}"


@pytest.mark.asyncio
async def test_get_tenant(client: AsyncClient, superadmin: User):
    created = (
        await client.post(
            "/api/v1/tenants",
            headers=auth(superadmin),
            json={"name": "GetMe", "slug": "getme"},
        )
    ).json()
    resp = await client.get(f"/api/v1/tenants/{created['id']}", headers=auth(superadmin))
    assert resp.status_code == 200
    assert resp.json()["slug"] == "getme"


@pytest.mark.asyncio
async def test_list_tenants(client: AsyncClient, superadmin: User):
    await client.post(
        "/api/v1/tenants",
        headers=auth(superadmin),
        json={"name": "Listed", "slug": "listed"},
    )
    resp = await client.get("/api/v1/tenants", headers=auth(superadmin))
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


@pytest.mark.asyncio
async def test_update_tenant_disable(client: AsyncClient, superadmin: User):
    created = (
        await client.post(
            "/api/v1/tenants",
            headers=auth(superadmin),
            json={"name": "Disable Me", "slug": "disableme"},
        )
    ).json()
    resp = await client.patch(
        f"/api/v1/tenants/{created['id']}",
        headers=auth(superadmin),
        json={"is_active": False},
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


@pytest.mark.asyncio
async def test_migrate_tenant_noop_on_sqlite(client: AsyncClient, superadmin: User):
    """migrate endpoint returns ok even on SQLite (schema creation is skipped)."""
    created = (
        await client.post(
            "/api/v1/tenants",
            headers=auth(superadmin),
            json={"name": "Migratable", "slug": "migratable"},
        )
    ).json()
    resp = await client.post(
        f"/api/v1/tenants/{created['id']}/migrate",
        headers=auth(superadmin),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Tenant middleware — branching logic
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_middleware_passthrough_when_flag_false(client: AsyncClient):
    """When MULTI_TENANT=false, all requests pass regardless of headers."""
    resp = await client.get("/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_middleware_disabled_tenant_blocked(db: AsyncSession, db_engine):
    """Middleware returns 403 when resolved tenant is disabled (MULTI_TENANT=true)."""
    from adminfoundry.middleware import tenant as tenant_mod
    from adminfoundry.main import app as main_app

    disabled = Tenant(name="Disabled Co", slug="disabled-co", is_active=False)
    db.add(disabled)
    await db.commit()

    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    # Patch AsyncSessionLocal used by TenantMiddleware to use the test engine
    import contextlib

    @contextlib.asynccontextmanager
    async def fake_session_local():
        async with factory() as s:
            yield s

    with patch.object(tenant_mod, "AsyncSessionLocal", fake_session_local):
        with patch("adminfoundry.settings.settings.MULTI_TENANT", True):
            transport = ASGITransport(app=main_app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/health", headers={"X-Tenant-Slug": "disabled-co"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_middleware_unknown_tenant_404(db: AsyncSession, db_engine):
    """Middleware returns 404 for unknown tenant slug."""
    from adminfoundry.middleware import tenant as tenant_mod
    from adminfoundry.main import app as main_app

    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    import contextlib

    @contextlib.asynccontextmanager
    async def fake_session_local():
        async with factory() as s:
            yield s

    with patch.object(tenant_mod, "AsyncSessionLocal", fake_session_local):
        with patch("adminfoundry.settings.settings.MULTI_TENANT", True):
            transport = ASGITransport(app=main_app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/health", headers={"X-Tenant-Slug": "no-such-tenant"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_middleware_active_tenant_passes(db: AsyncSession, db_engine):
    """Middleware stores tenant in state and lets request through."""
    from adminfoundry.middleware import tenant as tenant_mod
    from adminfoundry.main import app as main_app

    active = Tenant(name="Active Co", slug="active-co", is_active=True)
    db.add(active)
    await db.commit()

    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    import contextlib

    @contextlib.asynccontextmanager
    async def fake_session_local():
        async with factory() as s:
            yield s

    with patch.object(tenant_mod, "AsyncSessionLocal", fake_session_local):
        with patch("adminfoundry.settings.settings.MULTI_TENANT", True):
            transport = ASGITransport(app=main_app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/health", headers={"X-Tenant-Slug": "active-co"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_middleware_no_header_stores_none(db: AsyncSession, db_engine):
    """When MULTI_TENANT=true but no header, request proceeds (tenant=None)."""
    from adminfoundry.middleware import tenant as tenant_mod
    from adminfoundry.main import app as main_app

    with patch("adminfoundry.settings.settings.MULTI_TENANT", True):
        transport = ASGITransport(app=main_app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/health")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# get_tenant_db — requires tenant context in multi-tenant mode
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_admin_db_falls_back_to_shared_without_tenant():
    """get_admin_db returns a valid session even when no tenant is in request state."""
    from fastapi import FastAPI, Depends, Request
    from httpx import AsyncClient, ASGITransport
    from adminfoundry.database import get_admin_db
    from sqlalchemy.ext.asyncio import AsyncSession

    test_app = FastAPI()

    @test_app.get("/scoped")
    async def scoped(_db: AsyncSession = Depends(get_admin_db)):
        return {"ok": True}

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/scoped")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Regression: Phase 1+2 endpoints unaffected when MULTI_TENANT=false
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_phase1_auth_unaffected(client: AsyncClient, superadmin: User):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_phase2_users_unaffected(client: AsyncClient, superadmin: User):
    resp = await client.get("/api/v1/users", headers=auth(superadmin))
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_tenant_endpoints_superadmin_only(client: AsyncClient, db: AsyncSession):
    user = User(
        email="notadmin@example.com",
        hashed_password=hash_password("pw"),
        is_active=True,
        is_superadmin=False,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    resp = await client.get("/api/v1/tenants", headers=auth(user))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Tenant locale fields
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tenant_locale_fields_stored(db: AsyncSession):
    """Locale fields (timezone, language, date_format, date_pattern) are persisted."""
    t = Tenant(
        name="Locale Corp",
        slug="locale-corp",
        is_active=True,
        timezone="Europe/Berlin",
        language="de",
        date_format="eu",
        date_pattern=None,
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)

    assert t.timezone == "Europe/Berlin"
    assert t.language == "de"
    assert t.date_format == "eu"
    assert t.date_pattern is None


@pytest.mark.asyncio
async def test_tenant_locale_fields_independent_per_tenant(db: AsyncSession):
    """Two tenants can carry different locale settings without interfering."""
    t1 = Tenant(name="Acme", slug="acme", is_active=True, timezone="Europe/Berlin", language="de")
    t2 = Tenant(name="Globex", slug="globex", is_active=True, timezone="America/New_York", language="en")
    db.add(t1)
    db.add(t2)
    await db.commit()
    await db.refresh(t1)
    await db.refresh(t2)

    assert t1.timezone != t2.timezone
    assert t1.language != t2.language
