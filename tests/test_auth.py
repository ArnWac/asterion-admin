import pytest
from httpx import AsyncClient
from adminfoundry.models.user import User
from adminfoundry.auth import create_access_token, create_refresh_token


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient, superadmin: User):
    resp = await client.post("/api/v1/auth/login", json={
        "email": "admin@example.com",
        "password": "password123",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient, superadmin: User):
    resp = await client.post("/api/v1/auth/login", json={
        "email": "admin@example.com",
        "password": "wrongpassword",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_unknown_email(client: AsyncClient):
    resp = await client.post("/api/v1/auth/login", json={
        "email": "nobody@example.com",
        "password": "password123",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_inactive_user(client: AsyncClient, inactive_user: User):
    resp = await client.post("/api/v1/auth/login", json={
        "email": "inactive@example.com",
        "password": "password123",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_refresh_success(client: AsyncClient, superadmin: User):
    refresh_token = create_refresh_token(str(superadmin.id))
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    assert "access_token" in resp.json()


@pytest.mark.asyncio
async def test_refresh_rejects_access_token(client: AsyncClient, superadmin: User):
    access_token = create_access_token(str(superadmin.id))
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": access_token})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_refresh_invalid_token(client: AsyncClient):
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": "notavalidtoken"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_success(client: AsyncClient, superadmin: User):
    access_token = create_access_token(str(superadmin.id))
    resp = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "admin@example.com"
    assert data["is_superadmin"] is True


@pytest.mark.asyncio
async def test_me_no_token(client: AsyncClient):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code in (401, 403)  # HTTPBearer raises 403, deps return 401


@pytest.mark.asyncio
async def test_me_with_refresh_token_rejected(client: AsyncClient, superadmin: User):
    refresh_token = create_refresh_token(str(superadmin.id))
    resp = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {refresh_token}"})
    assert resp.status_code == 401


# Regression: protected fields must never appear in /me response
@pytest.mark.asyncio
async def test_me_no_protected_fields(client: AsyncClient, superadmin: User):
    access_token = create_access_token(str(superadmin.id))
    resp = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert "hashed_password" not in data
    assert "password" not in data


@pytest.mark.asyncio
async def test_login_response_no_protected_fields(client: AsyncClient, superadmin: User):
    resp = await client.post("/api/v1/auth/login", json={
        "email": "admin@example.com",
        "password": "password123",
    })
    data = resp.json()
    assert "hashed_password" not in data
    assert "password" not in data


@pytest.mark.asyncio
async def test_login_validation_error(client: AsyncClient):
    resp = await client.post("/api/v1/auth/login", json={"email": "notanemail", "password": ""})
    assert resp.status_code == 422
    body = resp.json()
    assert "errors" in body
