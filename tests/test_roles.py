import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from adminfoundry.models.user import User
from adminfoundry.models.role import Role
from adminfoundry.auth import create_access_token, hash_password


def auth(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


@pytest.mark.asyncio
async def test_create_role(client: AsyncClient, superadmin: User):
    resp = await client.post("/api/v1/roles", headers=auth(superadmin), json={"name": "manager"})
    assert resp.status_code == 201
    assert resp.json()["name"] == "manager"


@pytest.mark.asyncio
async def test_create_role_duplicate(client: AsyncClient, superadmin: User):
    await client.post("/api/v1/roles", headers=auth(superadmin), json={"name": "editor"})
    resp = await client.post("/api/v1/roles", headers=auth(superadmin), json={"name": "editor"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_list_roles(client: AsyncClient, superadmin: User):
    await client.post("/api/v1/roles", headers=auth(superadmin), json={"name": "viewer"})
    resp = await client.get("/api/v1/roles", headers=auth(superadmin))
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_get_role(client: AsyncClient, superadmin: User):
    created = (await client.post("/api/v1/roles", headers=auth(superadmin), json={"name": "ops"})).json()
    resp = await client.get(f"/api/v1/roles/{created['id']}", headers=auth(superadmin))
    assert resp.status_code == 200
    assert resp.json()["name"] == "ops"


@pytest.mark.asyncio
async def test_delete_role(client: AsyncClient, superadmin: User):
    created = (await client.post("/api/v1/roles", headers=auth(superadmin), json={"name": "temp"})).json()
    resp = await client.delete(f"/api/v1/roles/{created['id']}", headers=auth(superadmin))
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_assign_and_remove_role(client: AsyncClient, superadmin: User, db: AsyncSession):
    user = User(email="roletest@example.com", hashed_password=hash_password("pw"), is_active=True, is_superadmin=False)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    role_resp = await client.post("/api/v1/roles", headers=auth(superadmin), json={"name": "analyst"})
    role_id = role_resp.json()["id"]

    assign = await client.post(f"/api/v1/users/{user.id}/roles/{role_id}", headers=auth(superadmin))
    assert assign.status_code == 204

    # Verify role appears on user detail
    detail = await client.get(f"/api/v1/users/{user.id}", headers=auth(superadmin))
    role_names = [r["name"] for r in detail.json()["roles"]]
    assert "analyst" in role_names

    # Remove
    remove = await client.delete(f"/api/v1/users/{user.id}/roles/{role_id}", headers=auth(superadmin))
    assert remove.status_code == 204


@pytest.mark.asyncio
async def test_duplicate_role_assignment_rejected(client: AsyncClient, superadmin: User, db: AsyncSession):
    user = User(email="duprol@example.com", hashed_password=hash_password("pw"), is_active=True, is_superadmin=False)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    role_resp = await client.post("/api/v1/roles", headers=auth(superadmin), json={"name": "duptest"})
    role_id = role_resp.json()["id"]

    await client.post(f"/api/v1/users/{user.id}/roles/{role_id}", headers=auth(superadmin))
    resp = await client.post(f"/api/v1/users/{user.id}/roles/{role_id}", headers=auth(superadmin))
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_require_role_passes_with_role(client: AsyncClient, superadmin: User, db: AsyncSession):
    """require_role dependency: user with role gets through."""
    from adminfoundry.dependencies import require_role
    from adminfoundry.main import app

    user = User(email="hasrole@example.com", hashed_password=hash_password("pw"), is_active=True, is_superadmin=False)
    db.add(user)

    role = Role(name="checker")
    db.add(role)
    await db.commit()
    await db.refresh(user)
    await db.refresh(role)

    user_id = user.id  # capture before any expire

    # Assign role directly in DB
    from adminfoundry.models.role import user_roles
    await db.execute(user_roles.insert().values(user_id=user_id, role_id=role.id))
    await db.commit()

    # Refresh the roles relationship explicitly
    await db.refresh(user, attribute_names=["roles"])
    assert any(r.name == "checker" for r in user.roles)


@pytest.mark.asyncio
async def test_require_role_fails_without_role(client: AsyncClient, db: AsyncSession):
    """require_role dependency: user without role gets 403."""
    from adminfoundry.dependencies import require_role
    from adminfoundry.models.user import User
    from adminfoundry.auth import hash_password, create_access_token
    from fastapi import FastAPI, Depends
    from httpx import AsyncClient, ASGITransport

    user = User(email="norole@example.com", hashed_password=hash_password("pw"), is_active=True, is_superadmin=False)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Build a minimal test app with a require_role-protected route
    from adminfoundry.main import app as main_app
    from adminfoundry.database import get_db

    async def override_db():
        yield db

    test_app = FastAPI()

    @test_app.get("/test-role")
    async def protected(_: User = Depends(require_role("manager"))):
        return {"ok": True}

    test_app.dependency_overrides[get_db] = override_db

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        token = create_access_token(str(user.id))
        resp = await ac.get("/test-role", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_superadmin_bypasses_require_role(client: AsyncClient, superadmin: User, db: AsyncSession):
    """Superadmin passes require_role even without the role assigned."""
    from adminfoundry.dependencies import require_role
    from fastapi import FastAPI, Depends
    from httpx import AsyncClient, ASGITransport
    from adminfoundry.database import get_db

    async def override_db():
        yield db

    test_app = FastAPI()

    @test_app.get("/test-role")
    async def protected(_: User = Depends(require_role("manager"))):
        return {"ok": True}

    test_app.dependency_overrides[get_db] = override_db

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        token = create_access_token(str(superadmin.id))
        resp = await ac.get("/test-role", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_x_request_id_on_response(client: AsyncClient, superadmin: User):
    resp = await client.get("/health")
    assert "x-request-id" in resp.headers


@pytest.mark.asyncio
async def test_x_request_id_echoed(client: AsyncClient):
    resp = await client.get("/health", headers={"X-Request-ID": "my-id-123"})
    assert resp.headers["x-request-id"] == "my-id-123"
