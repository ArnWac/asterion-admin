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

from coreAdmin_api.models.user import User
from coreAdmin_api.models.tenant import Tenant
from coreAdmin_api.models.audit_log import AuditLog
from coreAdmin_api.models.impersonation_log import ImpersonationLog
from coreAdmin_api.auth import create_access_token, create_refresh_token, create_impersonation_token
from coreAdmin_api.token_blacklist import blacklist_token, is_blacklisted, clear_blacklist
from coreAdmin_api.auth import hash_password


def auth(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


# ---------------------------------------------------------------------------
# Unit: token blacklist
# ---------------------------------------------------------------------------

def test_blacklist_token_is_detected():
    import time
    jti = "test-jti-1"
    exp = time.time() + 3600
    blacklist_token(jti, exp)
    assert is_blacklisted(jti) is True


def test_blacklist_expired_token_is_not_blocked():
    import time
    jti = "test-jti-2"
    exp = time.time() - 1  # already expired
    blacklist_token(jti, exp)
    assert is_blacklisted(jti) is False


def test_unknown_jti_is_not_blacklisted():
    assert is_blacklisted("never-seen-jti") is False


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
    imp_token, _ = create_impersonation_token(str(superadmin.id), str(superadmin.id))
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": imp_token})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_impersonation_token_rejected_on_superadmin_route(
    client: AsyncClient, superadmin: User, db: AsyncSession
):
    """An impersonation token must not access superadmin-only routes."""
    imp_token, _ = create_impersonation_token(str(superadmin.id), str(superadmin.id))
    resp = await client.get(
        "/api/v1/admin",
        headers={"Authorization": f"Bearer {imp_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_impersonation_token_allows_normal_route(
    client: AsyncClient, superadmin: User, db: AsyncSession
):
    """Impersonation token is valid for non-superadmin routes like /me."""
    imp_token, _ = create_impersonation_token(str(superadmin.id), str(superadmin.id))
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
