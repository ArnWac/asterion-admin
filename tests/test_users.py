import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from adminfoundry.models.user import User
from adminfoundry.models.role import Role
from adminfoundry.auth import create_access_token


def auth(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


@pytest.mark.asyncio
async def test_list_users_superadmin(client: AsyncClient, superadmin: User):
    resp = await client.get("/api/v1/users", headers=auth(superadmin))
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_list_users_forbidden_non_superadmin(client: AsyncClient, db: AsyncSession):
    from adminfoundry.auth import hash_password
    user = User(email="plain@example.com", hashed_password=hash_password("pw"), is_active=True, is_superadmin=False)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    resp = await client.get("/api/v1/users", headers=auth(user))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_user(client: AsyncClient, superadmin: User):
    resp = await client.post("/api/v1/users", headers=auth(superadmin), json={
        "email": "newuser@example.com",
        "password": "secret123",
        "full_name": "New User",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "newuser@example.com"
    assert "hashed_password" not in data
    assert "password" not in data


@pytest.mark.asyncio
async def test_create_user_duplicate_email(client: AsyncClient, superadmin: User):
    payload = {"email": "dup@example.com", "password": "pw123"}
    await client.post("/api/v1/users", headers=auth(superadmin), json=payload)
    resp = await client.post("/api/v1/users", headers=auth(superadmin), json=payload)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_get_user(client: AsyncClient, superadmin: User):
    resp = await client.get(f"/api/v1/users/{superadmin.id}", headers=auth(superadmin))
    assert resp.status_code == 200
    assert resp.json()["id"] == str(superadmin.id)


@pytest.mark.asyncio
async def test_update_user(client: AsyncClient, superadmin: User):
    resp = await client.patch(
        f"/api/v1/users/{superadmin.id}",
        headers=auth(superadmin),
        json={"full_name": "Updated Name"},
    )
    assert resp.status_code == 200
    assert resp.json()["full_name"] == "Updated Name"


@pytest.mark.asyncio
async def test_soft_delete_user(client: AsyncClient, superadmin: User, db: AsyncSession):
    from adminfoundry.auth import hash_password
    target = User(email="todelete@example.com", hashed_password=hash_password("pw"), is_active=True, is_superadmin=False)
    db.add(target)
    await db.commit()
    await db.refresh(target)

    resp = await client.delete(f"/api/v1/users/{target.id}", headers=auth(superadmin))
    assert resp.status_code == 204

    # Verify soft delete — row still exists, is_active=False
    await db.refresh(target)
    assert target.is_active is False


@pytest.mark.asyncio
async def test_user_response_no_protected_fields(client: AsyncClient, superadmin: User):
    resp = await client.get(f"/api/v1/users/{superadmin.id}", headers=auth(superadmin))
    data = resp.json()
    assert "hashed_password" not in data
    assert "password" not in data
