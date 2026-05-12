"""
Audit, impersonation, logout tests.
Covers: blacklist logic, logout, refresh restriction, impersonation flow,
audit log listing, and e2e critical flows.
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from adminfoundry.models.user import User
from adminfoundry.models.tenant import Tenant
from adminfoundry.models.audit_log import AuditLog
from adminfoundry.models.impersonation_log import ImpersonationLog
from adminfoundry.models.role import Role
from adminfoundry.auth import create_access_token, create_refresh_token, create_impersonation_token
from adminfoundry.token_blacklist import blacklist_token, is_blacklisted
from adminfoundry.auth import hash_password


def auth(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


# ---------------------------------------------------------------------------
# Unit: token blacklist
# ---------------------------------------------------------------------------

async def test_blacklist_token_is_detected(db: AsyncSession):
    import time
    jti = "test-jti-1"
    exp = time.time() + 3600
    await blacklist_token(jti, exp, db)
    assert await is_blacklisted(jti, db) is True


async def test_blacklist_expired_token_is_not_blocked(db: AsyncSession):
    import time
    jti = "test-jti-2"
    exp = time.time() - 1  # already expired
    await blacklist_token(jti, exp, db)
    assert await is_blacklisted(jti, db) is False


async def test_unknown_jti_is_not_blacklisted(db: AsyncSession):
    assert await is_blacklisted("never-seen-jti", db) is False


# ---------------------------------------------------------------------------
# Logout — e2e flow 1: login -> logout -> same token rejected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_logout_invalidates_access_token(client: AsyncClient, superadmin: User):
    # Login to get a fresh token pair
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )
    assert login.status_code == 200
    access_token = login.json()["access_token"]

    headers = {"Authorization": f"Bearer {access_token}"}

    # Token works before logout
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200

    # Logout
    logout = await client.post("/api/v1/auth/logout", headers=headers)
    assert logout.status_code == 204

    # Same access token is now rejected
    me_after = await client.get("/api/v1/auth/me", headers=headers)
    assert me_after.status_code == 401


@pytest.mark.asyncio
async def test_refresh_still_works_after_logout(client: AsyncClient, superadmin: User):
    """Logout revokes access token only; refresh token remains valid."""
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )
    access_token = login.json()["access_token"]
    refresh_token = login.json()["refresh_token"]

    # Logout the access token
    await client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    # Refresh should still work
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    assert "access_token" in resp.json()


# ---------------------------------------------------------------------------
# Impersonation token restrictions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_impersonation_token_cannot_be_refreshed(client: AsyncClient, superadmin: User, db: AsyncSession):
    """Impersonation tokens are access-type; passing one as refresh token must fail."""
    imp_token, _ = create_impersonation_token(str(superadmin.id), str(superadmin.id), "00000000-0000-0000-0000-000000000000")
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": imp_token})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_impersonation_token_returns_tenant_scoped_registry(
    client: AsyncClient, superadmin: User, db: AsyncSession
):
    """Impersonation token may read the admin registry but only sees tenant-scoped models."""
    imp_token, _ = create_impersonation_token(str(superadmin.id), str(superadmin.id), "00000000-0000-0000-0000-000000000000")
    resp = await client.get(
        "/api/v1/admin",
        headers={"Authorization": f"Bearer {imp_token}"},
    )
    assert resp.status_code == 200
    # Only tenant-scoped models should be returned in tenant context
    models = resp.json()["models"]
    model_names = [m["model"] for m in models]
    assert "users" not in model_names        # tenant_scoped=False — excluded
    assert "audit_logs" not in model_names   # tenant_scoped=False — excluded


@pytest.mark.asyncio
async def test_impersonation_token_rejected_on_write_superadmin_route(
    client: AsyncClient, superadmin: User, db: AsyncSession
):
    """Impersonation token must not access write superadmin-only routes (e.g. user create)."""
    from uuid import uuid4
    imp_token, _ = create_impersonation_token(str(superadmin.id), str(superadmin.id), "00000000-0000-0000-0000-000000000000")
    resp = await client.post(
        "/api/v1/users",
        headers={"Authorization": f"Bearer {imp_token}"},
        json={"email": f"x{uuid4()}@x.com", "password": "pw"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_impersonation_token_allows_normal_route(
    client: AsyncClient, superadmin: User, db: AsyncSession
):
    """Impersonation token is valid for non-superadmin routes like /me."""
    imp_token, _ = create_impersonation_token(str(superadmin.id), str(superadmin.id), "00000000-0000-0000-0000-000000000000")
    resp = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {imp_token}"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Impersonation flow — e2e flow 2: create tenant -> impersonate -> revoke -> rejected
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def tenant(db: AsyncSession) -> Tenant:
    t = Tenant(name="Acme Corp", slug="acme", is_active=True)
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


@pytest_asyncio.fixture
async def plain_user(db: AsyncSession) -> User:
    u = User(
        email="plain@acme.com",
        hashed_password=hash_password("pw"),
        is_active=True,
        is_superadmin=False,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest.mark.asyncio
async def test_impersonate_creates_log(
    client: AsyncClient, superadmin: User, tenant: Tenant, plain_user: User, db: AsyncSession
):
    resp = await client.post(
        f"/api/v1/tenants/{tenant.id}/impersonate",
        headers=auth(superadmin),
        json={"target_user_id": str(plain_user.id)},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "impersonation_log_id" in data

    log = (
        await db.execute(select(ImpersonationLog).where(ImpersonationLog.id == data["impersonation_log_id"]))
    ).scalar_one_or_none()
    assert log is not None
    assert str(log.superadmin_id) == str(superadmin.id)
    assert str(log.target_user_id) == str(plain_user.id)
    assert log.revoked_at is None


@pytest.mark.asyncio
async def test_revoke_impersonation_blacklists_token(
    client: AsyncClient, superadmin: User, tenant: Tenant, plain_user: User, db: AsyncSession
):
    # Start impersonation
    imp_resp = await client.post(
        f"/api/v1/tenants/{tenant.id}/impersonate",
        headers=auth(superadmin),
        json={"target_user_id": str(plain_user.id)},
    )
    data = imp_resp.json()
    imp_token = data["access_token"]

    # Decode JTI from the log
    log = (
        await db.execute(select(ImpersonationLog).where(ImpersonationLog.id == data["impersonation_log_id"]))
    ).scalar_one()
    jti = log.jti

    # Token works before revoke
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {imp_token}"})
    assert me.status_code == 200

    # Revoke
    revoke = await client.post(
        f"/api/v1/tenants/{tenant.id}/impersonate/revoke",
        headers=auth(superadmin),
        json={"jti": jti},
    )
    assert revoke.status_code == 200

    # Token is now rejected
    me_after = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {imp_token}"})
    assert me_after.status_code == 401


@pytest.mark.asyncio
async def test_revoke_marks_log_revoked(
    client: AsyncClient, superadmin: User, tenant: Tenant, plain_user: User, db: AsyncSession
):
    imp_resp = await client.post(
        f"/api/v1/tenants/{tenant.id}/impersonate",
        headers=auth(superadmin),
        json={"target_user_id": str(plain_user.id)},
    )
    log_id = imp_resp.json()["impersonation_log_id"]
    log = (await db.execute(select(ImpersonationLog).where(ImpersonationLog.id == log_id))).scalar_one()

    await client.post(
        f"/api/v1/tenants/{tenant.id}/impersonate/revoke",
        headers=auth(superadmin),
        json={"jti": log.jti},
    )

    await db.refresh(log)
    assert log.revoked_at is not None


@pytest.mark.asyncio
async def test_revoke_twice_returns_conflict(
    client: AsyncClient, superadmin: User, tenant: Tenant, plain_user: User, db: AsyncSession
):
    imp_resp = await client.post(
        f"/api/v1/tenants/{tenant.id}/impersonate",
        headers=auth(superadmin),
        json={"target_user_id": str(plain_user.id)},
    )
    log_id = imp_resp.json()["impersonation_log_id"]
    log = (await db.execute(select(ImpersonationLog).where(ImpersonationLog.id == log_id))).scalar_one()

    await client.post(
        f"/api/v1/tenants/{tenant.id}/impersonate/revoke",
        headers=auth(superadmin),
        json={"jti": log.jti},
    )
    resp2 = await client.post(
        f"/api/v1/tenants/{tenant.id}/impersonate/revoke",
        headers=auth(superadmin),
        json={"jti": log.jti},
    )
    assert resp2.status_code == 409


# ---------------------------------------------------------------------------
# Audit log listing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_audit_log_list(client: AsyncClient, superadmin: User, db: AsyncSession):
    resp = await client.get("/api/v1/audit", headers=auth(superadmin))
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "items" in data


@pytest.mark.asyncio
async def test_audit_log_requires_superadmin(client: AsyncClient, db: AsyncSession):
    user = User(
        email="plain3@x.com",
        hashed_password=hash_password("pw"),
        is_active=True,
        is_superadmin=False,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    resp = await client.get("/api/v1/audit", headers=auth(user))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Same-origin impersonation: tenant_id in audit log + dashboard filtering
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_impersonation_started_audit_has_tenant_id(
    client: AsyncClient, superadmin: User, db: AsyncSession
):
    """impersonation_started audit log entry must carry the target tenant_id."""
    tenant = Tenant(name="Imp Tenant", slug="imp-tenant", is_active=True)
    plain = User(
        email="plain@imp.com",
        hashed_password=hash_password("pw"),
        is_active=True,
        is_superadmin=False,
    )
    db.add(tenant)
    db.add(plain)
    await db.commit()
    await db.refresh(tenant)
    await db.refresh(plain)

    resp = await client.post(
        f"/api/v1/tenants/{tenant.id}/impersonate",
        headers=auth(superadmin),
        json={"target_user_id": str(plain.id)},
    )
    assert resp.status_code == 200

    log = (await db.execute(
        select(AuditLog).where(AuditLog.action == "impersonation_started")
    )).scalar_one_or_none()
    assert log is not None
    assert log.tenant_id == tenant.id
    assert log.actor == superadmin.email


@pytest.mark.asyncio
async def test_audit_log_tenant_id_from_impersonation_token(
    client: AsyncClient, superadmin: User, db: AsyncSession
):
    """Audit log entries must carry tenant_id when written via a same-origin impersonation
    token — i.e. when TenantMiddleware did not set request.state.tenant."""
    tenant = Tenant(name="Audit Tenant", slug="audit-tenant", is_active=True)
    role = Role(name="imp-audit-role")
    db.add(tenant)
    db.add(role)
    await db.commit()
    await db.refresh(tenant)
    await db.refresh(role)

    imp_token, _ = create_impersonation_token(
        str(superadmin.id), str(superadmin.id), str(tenant.id)
    )
    resp = await client.put(
        f"/api/v1/admin/permission-matrix/{role.id}",
        headers={"Authorization": f"Bearer {imp_token}"},
        json=[{
            "model_name": "roles",
            "can_list": True, "can_create": False, "can_update": False, "can_delete": False,
        }],
    )
    assert resp.status_code == 204

    log = (await db.execute(
        select(AuditLog)
        .where(AuditLog.object_id == str(role.id), AuditLog.action == "updated")
    )).scalar_one_or_none()
    assert log is not None
    assert log.tenant_id == tenant.id


@pytest.mark.asyncio
async def test_dashboard_hides_global_models_during_impersonation(
    client: AsyncClient, superadmin: User, db: AsyncSession
):
    """ModelCountsWidget must omit tenant_scoped=False models (users, audit_logs, tenants)
    when the superadmin is using a same-origin impersonation token."""
    tenant = Tenant(name="Dash Tenant", slug="dash-tenant", is_active=True)
    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)

    imp_token, _ = create_impersonation_token(
        str(superadmin.id), str(superadmin.id), str(tenant.id)
    )
    resp = await client.get(
        "/api/v1/admin/dashboard",
        headers={"Authorization": f"Bearer {imp_token}"},
    )
    assert resp.status_code == 200
    widgets = resp.json()["widgets"]
    counts = next((w for w in widgets if w["type"] == "counts"), None)
    if counts:  # widget only present when models are registered
        model_names = [r["model"] for r in counts["data"]["rows"]]
        assert "users" not in model_names       # tenant_scoped=False — must be hidden
        assert "audit_logs" not in model_names  # tenant_scoped=False — must be hidden


# ---------------------------------------------------------------------------
# Regression: earlier phase endpoints unaffected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_login_still_works(client: AsyncClient, superadmin: User):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_users_endpoint_still_works(client: AsyncClient, superadmin: User):
    resp = await client.get("/api/v1/users", headers=auth(superadmin))
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_admin_crud_still_works(client: AsyncClient, superadmin: User):
    resp = await client.get("/api/v1/admin", headers=auth(superadmin))
    assert resp.status_code == 200
