"""Phase 12 — Security Hardening, Sessions, Observability."""
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from adminfoundry.middleware.rate_limit import reset_rate_limiter
from adminfoundry.models.user import User
from adminfoundry.auth import hash_password, create_access_token_with_iat
from adminfoundry.observability.admin_metrics import get_snapshot, reset as reset_metrics
from adminfoundry.services.session_security import session_security


@pytest_asyncio.fixture
async def admin_token(db: AsyncSession) -> str:
    user = User(
        email="admin12@example.com",
        hashed_password=hash_password("pass"),
        full_name="Admin12",
        is_active=True,
        is_superadmin=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return create_access_token_with_iat(str(user.id))


@pytest.fixture(autouse=True)
def cleanup():
    reset_rate_limiter()
    session_security.clear()
    reset_metrics()
    yield
    reset_rate_limiter()
    session_security.clear()
    reset_metrics()


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_security_headers_present(client: AsyncClient):
    r = await client.get("/health")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"
    assert "strict-transport-security" in r.headers


# ---------------------------------------------------------------------------
# Rate limiting (smoke test — low limit in config)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rate_limit_returns_429_after_limit(client: AsyncClient, db: AsyncSession):
    # The login rate limit is 10/60s — send 11 requests with wrong credentials
    for _ in range(10):
        await client.post(
            "/api/v1/auth/login",
            json={"email": "noone@x.com", "password": "wrong"},
        )
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": "noone@x.com", "password": "wrong"},
    )
    assert r.status_code == 429
    assert "Retry-After" in r.headers


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_login_registers_session(client: AsyncClient, db: AsyncSession):
    user = User(
        email="sess@example.com",
        hashed_password=hash_password("pass"),
        is_active=True,
        is_superadmin=True,
    )
    db.add(user)
    await db.commit()

    r = await client.post(
        "/api/v1/auth/login",
        json={"email": "sess@example.com", "password": "pass"},
    )
    assert r.status_code == 200
    # A session record should now exist
    assert len(session_security.list_all_active()) >= 1


@pytest.mark.asyncio
async def test_list_sessions(client: AsyncClient, db: AsyncSession):
    user = User(
        email="sess2@example.com",
        hashed_password=hash_password("pass"),
        is_active=True,
        is_superadmin=True,
    )
    db.add(user)
    await db.commit()

    login_r = await client.post(
        "/api/v1/auth/login",
        json={"email": "sess2@example.com", "password": "pass"},
    )
    token = login_r.json()["access_token"]

    r = await client.get("/api/v1/auth/sessions", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) >= 1


@pytest.mark.asyncio
async def test_revoke_session(client: AsyncClient, db: AsyncSession):
    user = User(
        email="sess3@example.com",
        hashed_password=hash_password("pass"),
        is_active=True,
        is_superadmin=True,
    )
    db.add(user)
    await db.commit()

    login_r = await client.post(
        "/api/v1/auth/login",
        json={"email": "sess3@example.com", "password": "pass"},
    )
    token = login_r.json()["access_token"]

    sessions_r = await client.get(
        "/api/v1/auth/sessions", headers={"Authorization": f"Bearer {token}"}
    )
    jti = sessions_r.json()[0]["jti"]

    revoke_r = await client.delete(
        f"/api/v1/auth/sessions/{jti}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert revoke_r.status_code == 204

    # Token should now be rejected
    me_r = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_r.status_code == 401


# ---------------------------------------------------------------------------
# Step-up auth
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_step_up_fresh_token(client: AsyncClient, db: AsyncSession):
    user = User(
        email="stepup@example.com",
        hashed_password=hash_password("pass"),
        is_active=True,
        is_superadmin=True,
    )
    db.add(user)
    await db.commit()

    login_r = await client.post(
        "/api/v1/auth/login",
        json={"email": "stepup@example.com", "password": "pass"},
    )
    token = login_r.json()["access_token"]
    r = await client.post("/api/v1/auth/step-up", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["step_up"] is True


# ---------------------------------------------------------------------------
# Metrics snapshot
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_metrics_snapshot_accessible(client: AsyncClient, admin_token: str):
    r = await client.get(
        "/api/v1/admin/metrics",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "request_count" in body
    assert "audit_write_failures" in body


@pytest.mark.asyncio
async def test_metrics_non_superadmin_forbidden(client: AsyncClient, db: AsyncSession):
    user = User(
        email="metrics_nonadmin@example.com",
        hashed_password=hash_password("pass"),
        is_active=True,
        is_superadmin=False,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    token = create_access_token_with_iat(str(user.id))

    r = await client.get(
        "/api/v1/admin/metrics",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403
