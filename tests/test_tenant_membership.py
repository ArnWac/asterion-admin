"""Regression tests for TenantMembership authorization enforcement.

All tests verify the security invariant:
    Tenant access is a backend boundary, not a UI/navigation concern.
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from adminfoundry.auth import create_access_token, hash_password
from adminfoundry.models.role import Role
from adminfoundry.models.tenant import Tenant
from adminfoundry.models.tenant_membership import TenantMembership
from adminfoundry.models.user import User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def auth(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def tenant_header(slug: str) -> dict:
    # basic_multi app uses subdomain resolution — send slug as subdomain of test host
    return {"host": f"{slug}.test"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def tenant_a(db: AsyncSession) -> Tenant:
    t = Tenant(name="Tenant A", slug="tenant-a", is_active=True)
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


@pytest_asyncio.fixture
async def tenant_b(db: AsyncSession) -> Tenant:
    t = Tenant(name="Tenant B", slug="tenant-b", is_active=True)
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


@pytest_asyncio.fixture
async def role_a(db: AsyncSession, tenant_a: Tenant) -> Role:
    r = Role(name="tenant_admin", tenant_id=tenant_a.id, description="admin")
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


@pytest_asyncio.fixture
async def role_b(db: AsyncSession, tenant_b: Tenant) -> Role:
    r = Role(name="tenant_admin", tenant_id=tenant_b.id, description="admin")
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


@pytest_asyncio.fixture
async def user_a(db: AsyncSession) -> User:
    """Regular user, no global superadmin."""
    u = User(
        email="user-a@example.com",
        hashed_password=hash_password("password123"),
        is_active=True,
        is_superadmin=False,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def membership_a(db: AsyncSession, user_a: User, tenant_a: Tenant, role_a: Role) -> TenantMembership:
    """Active membership of user_a in tenant_a with tenant_admin role."""
    m = TenantMembership(user_id=user_a.id, tenant_id=tenant_a.id, is_active=True)
    db.add(m)
    await db.flush()
    await db.refresh(m)
    # Assign the role through the membership
    m.roles.append(role_a)
    await db.commit()
    await db.refresh(m)
    return m


# ---------------------------------------------------------------------------
# 1. User cannot login to an unassigned tenant
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_user_cannot_login_to_unassigned_tenant(
    client: AsyncClient,
    db: AsyncSession,
    user_a: User,
    tenant_a: Tenant,
    tenant_b: Tenant,
    membership_a: TenantMembership,
):
    """user_a has membership in tenant_a. Login to tenant_b must be 403."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "user-a@example.com", "password": "password123"},
        headers=tenant_header(tenant_b.slug),
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_user_can_login_to_assigned_tenant(
    client: AsyncClient,
    db: AsyncSession,
    user_a: User,
    tenant_a: Tenant,
    membership_a: TenantMembership,
):
    """user_a has membership in tenant_a. Login to tenant_a must succeed."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "user-a@example.com", "password": "password123"},
        headers=tenant_header(tenant_a.slug),
    )
    assert resp.status_code == 200, resp.text
    assert "access_token" in resp.json()


# ---------------------------------------------------------------------------
# 2. Valid token from tenant A cannot access tenant B API
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_user_token_from_tenant_a_cannot_access_tenant_b_api(
    client: AsyncClient,
    db: AsyncSession,
    user_a: User,
    tenant_a: Tenant,
    tenant_b: Tenant,
    membership_a: TenantMembership,
):
    """user_a's access token (issued in tenant_a context) cannot access tenant_b dashboard."""
    headers = {**auth(user_a), **tenant_header(tenant_b.slug)}
    resp = await client.get("/api/v1/admin/dashboard", headers=headers)
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_user_with_no_membership_is_blocked(
    client: AsyncClient,
    db: AsyncSession,
    user_a: User,
    tenant_a: Tenant,
):
    """user_a has no membership anywhere. Any tenant-scoped request must be 403."""
    headers = {**auth(user_a), **tenant_header(tenant_a.slug)}
    resp = await client.get("/api/v1/admin/dashboard", headers=headers)
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# 3. Roles are tenant-scoped — tenant A role does not count in tenant B
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_roles_are_tenant_scoped(
    client: AsyncClient,
    db: AsyncSession,
    user_a: User,
    tenant_a: Tenant,
    tenant_b: Tenant,
    membership_a: TenantMembership,
):
    """user_a is tenant_admin in tenant_a. Accessing tenant_b admin must be 403."""
    # Verify tenant_a access works (tenant_admin role resolves)
    headers_a = {**auth(user_a), **tenant_header(tenant_a.slug)}
    resp_a = await client.get("/api/v1/admin", headers=headers_a)
    assert resp_a.status_code == 200, resp_a.text

    # tenant_b access must fail — user has no membership there
    headers_b = {**auth(user_a), **tenant_header(tenant_b.slug)}
    resp_b = await client.get("/api/v1/admin", headers=headers_b)
    assert resp_b.status_code == 403, resp_b.text


# ---------------------------------------------------------------------------
# 4. Inactive membership denies access
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inactive_membership_denies_login(
    client: AsyncClient,
    db: AsyncSession,
    user_a: User,
    tenant_a: Tenant,
):
    """Inactive membership must block login even if user and tenant are active."""
    inactive = TenantMembership(user_id=user_a.id, tenant_id=tenant_a.id, is_active=False)
    db.add(inactive)
    await db.commit()

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "user-a@example.com", "password": "password123"},
        headers=tenant_header(tenant_a.slug),
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_inactive_membership_denies_api_access(
    client: AsyncClient,
    db: AsyncSession,
    user_a: User,
    tenant_a: Tenant,
):
    """Inactive membership must block API access even with a valid JWT."""
    inactive = TenantMembership(user_id=user_a.id, tenant_id=tenant_a.id, is_active=False)
    db.add(inactive)
    await db.commit()

    headers = {**auth(user_a), **tenant_header(tenant_a.slug)}
    resp = await client.get("/api/v1/admin/dashboard", headers=headers)
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# 5. Membership role from another tenant does not count
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_membership_role_from_other_tenant_does_not_count(
    client: AsyncClient,
    db: AsyncSession,
    user_a: User,
    tenant_a: Tenant,
    tenant_b: Tenant,
    role_b: Role,
):
    """user_a has membership+role in tenant_b but not in tenant_a.
    Accessing tenant_a admin must be 403 — the tenant_b role must not count.
    """
    # Give user_a membership and tenant_admin role in tenant_b
    m_b = TenantMembership(user_id=user_a.id, tenant_id=tenant_b.id, is_active=True)
    db.add(m_b)
    await db.flush()
    await db.refresh(m_b)
    m_b.roles.append(role_b)
    await db.commit()

    # tenant_a access — no membership there — must be 403
    headers_a = {**auth(user_a), **tenant_header(tenant_a.slug)}
    resp = await client.get("/api/v1/admin", headers=headers_a)
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# 6. Superadmin impersonation token must match tenant
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_superadmin_impersonation_token_must_match_tenant(
    client: AsyncClient,
    db: AsyncSession,
    superadmin: User,
    tenant_a: Tenant,
    tenant_b: Tenant,
):
    """Impersonation token for tenant_a must not grant access in tenant_b context."""
    from adminfoundry.auth import create_impersonation_token
    token, _ = create_impersonation_token(str(superadmin.id), str(superadmin.id), str(tenant_a.id))

    headers = {
        "Authorization": f"Bearer {token}",
        **tenant_header(tenant_b.slug),
    }
    resp = await client.get("/api/v1/admin", headers=headers)
    # _check_model_access rejects the impersonation token because tenant_id mismatch
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# 7. Navbar hiding is not authorization — direct API call must still be rejected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hidden_navbar_does_not_grant_authorization(
    client: AsyncClient,
    db: AsyncSession,
    user_a: User,
    tenant_a: Tenant,
    tenant_b: Tenant,
    membership_a: TenantMembership,
):
    """Even if the UI hides tenant_b entries for user_a, direct API calls must fail."""
    # Directly call tenant_b admin endpoint — no membership there
    headers = {**auth(user_a), **tenant_header(tenant_b.slug)}
    resp = await client.get("/api/v1/admin/dashboard", headers=headers)
    assert resp.status_code == 403, resp.text

    # Same for the model registry
    resp2 = await client.get("/api/v1/admin", headers=headers)
    assert resp2.status_code == 403, resp2.text


# ---------------------------------------------------------------------------
# 8. Multi-tenant user can access both tenants they belong to
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multi_tenant_user_can_access_both_tenants(
    client: AsyncClient,
    db: AsyncSession,
    user_a: User,
    tenant_a: Tenant,
    tenant_b: Tenant,
    role_a: Role,
    role_b: Role,
    membership_a: TenantMembership,
):
    """A user with memberships in two tenants can access both."""
    m_b = TenantMembership(user_id=user_a.id, tenant_id=tenant_b.id, is_active=True)
    db.add(m_b)
    await db.flush()
    await db.refresh(m_b)
    m_b.roles.append(role_b)
    await db.commit()

    headers_a = {**auth(user_a), **tenant_header(tenant_a.slug)}
    headers_b = {**auth(user_a), **tenant_header(tenant_b.slug)}

    resp_a = await client.get("/api/v1/admin", headers=headers_a)
    assert resp_a.status_code == 200, resp_a.text

    resp_b = await client.get("/api/v1/admin", headers=headers_b)
    assert resp_b.status_code == 200, resp_b.text


# ---------------------------------------------------------------------------
# 9. Root panel (no tenant context) is unaffected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_superadmin_root_panel_unaffected(
    client: AsyncClient,
    db: AsyncSession,
    superadmin: User,
):
    """Superadmin accessing root panel (no tenant header) must still work."""
    resp = await client.get("/api/v1/admin", headers=auth(superadmin))
    assert resp.status_code == 200, resp.text
