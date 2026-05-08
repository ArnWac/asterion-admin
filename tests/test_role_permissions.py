"""Tests for the RolePermission model and PolicyEngine DB-backed caps."""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from adminfoundry.auth import create_access_token, hash_password
from adminfoundry.authz.policy_engine import PolicyEngine
from adminfoundry.authz.role_caps import fetch_model_caps, fetch_all_model_caps
from adminfoundry.models.role import Role
from adminfoundry.models.role_permission import RolePermission
from adminfoundry.models.user import User


def auth(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


async def _make_user(db: AsyncSession, email: str, *, superadmin=False) -> User:
    u = User(
        email=email,
        hashed_password=hash_password("pass"),
        is_active=True,
        is_superadmin=superadmin,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _make_role(db: AsyncSession, name: str) -> Role:
    r = Role(name=name)
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


async def _grant(db, role: Role, model_name: str, **caps) -> RolePermission:
    rp = RolePermission(
        role_id=role.id,
        model_name=model_name,
        can_list=caps.get("can_list", True),
        can_create=caps.get("can_create", False),
        can_update=caps.get("can_update", False),
        can_delete=caps.get("can_delete", False),
    )
    db.add(rp)
    await db.commit()
    await db.refresh(rp)
    return rp


# ---------------------------------------------------------------------------
# Unit: fetch_model_caps
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_model_caps_no_roles(db: AsyncSession):
    user = await _make_user(db, "noroles@example.com")
    result = await fetch_model_caps(user, "users", db)
    assert result is None


@pytest.mark.asyncio
async def test_fetch_model_caps_no_permission_record(db: AsyncSession):
    user = await _make_user(db, "u@example.com")
    role = await _make_role(db, "editor")
    # assign role to user
    from adminfoundry.models.role import user_roles
    await db.execute(user_roles.insert().values(user_id=user.id, role_id=role.id))
    await db.commit()
    await db.refresh(user)

    result = await fetch_model_caps(user, "articles", db)
    assert result is None  # no DB record → fallback to ModelAdmin config


@pytest.mark.asyncio
async def test_fetch_model_caps_returns_merged_caps(db: AsyncSession):
    user = await _make_user(db, "u@example.com")
    role = await _make_role(db, "editor")
    from adminfoundry.models.role import user_roles
    await db.execute(user_roles.insert().values(user_id=user.id, role_id=role.id))
    await db.commit()
    await db.refresh(user)

    await _grant(db, role, "articles", can_list=True, can_create=True, can_update=False, can_delete=False)

    caps = await fetch_model_caps(user, "articles", db)
    assert caps is not None
    assert caps["can_list"] is True
    assert caps["can_create"] is True
    assert caps["can_update"] is False
    assert caps["can_delete"] is False
    assert caps["can_read"] is True  # derived from can_list


@pytest.mark.asyncio
async def test_fetch_model_caps_or_merge_multiple_roles(db: AsyncSession):
    user = await _make_user(db, "u@example.com")
    r1 = await _make_role(db, "reader")
    r2 = await _make_role(db, "writer")
    from adminfoundry.models.role import user_roles
    await db.execute(user_roles.insert().values(user_id=user.id, role_id=r1.id))
    await db.execute(user_roles.insert().values(user_id=user.id, role_id=r2.id))
    await db.commit()
    await db.refresh(user)

    await _grant(db, r1, "articles", can_list=True, can_create=False)
    await _grant(db, r2, "articles", can_list=True, can_create=True, can_delete=True)

    caps = await fetch_model_caps(user, "articles", db)
    assert caps["can_create"] is True   # OR-merged
    assert caps["can_delete"] is True   # from r2
    assert caps["can_update"] is False  # neither granted


# ---------------------------------------------------------------------------
# Unit: PolicyEngine.effective_model_caps with db_caps
# ---------------------------------------------------------------------------

def test_policy_engine_uses_db_caps_over_admin_config():
    from adminfoundry.admin.model_admin import ModelAdmin

    class LockedAdmin(ModelAdmin):
        model = User
        admin_only = True  # would deny non-superadmin without db_caps

    pe = PolicyEngine()

    class _FakeUser:
        is_superadmin = False
        roles = []

    user = _FakeUser()
    db_caps = dict(can_list=True, can_create=False, can_read=True, can_update=True, can_delete=False)
    caps = pe.effective_model_caps(user, LockedAdmin(), {}, db_caps=db_caps)
    assert caps == db_caps


def test_policy_engine_superadmin_ignores_db_caps():
    from adminfoundry.admin.model_admin import ModelAdmin

    class SomeAdmin(ModelAdmin):
        model = User

    pe = PolicyEngine()

    class _SuperUser:
        is_superadmin = True
        roles = []

    user = _SuperUser()
    # Even a very restrictive db_caps should not affect a superadmin
    db_caps = dict(can_list=False, can_create=False, can_read=False, can_update=False, can_delete=False)
    caps = pe.effective_model_caps(user, SomeAdmin(), {}, db_caps=db_caps)
    assert all(caps.values())  # superadmin always gets True for all


# ---------------------------------------------------------------------------
# HTTP: capabilities endpoint reflects DB-backed caps
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_capabilities_endpoint_reflects_role_permissions(
    client: AsyncClient, db: AsyncSession
):
    user = await _make_user(db, "cap@example.com")
    role = await _make_role(db, "readonly")
    from adminfoundry.models.role import user_roles
    await db.execute(user_roles.insert().values(user_id=user.id, role_id=role.id))
    await db.commit()
    await db.refresh(user)

    # Grant read-only on "users" model
    await _grant(db, role, "users", can_list=True, can_create=False, can_update=False, can_delete=False)

    resp = await client.get("/api/v1/admin/capabilities", headers=auth(user))
    assert resp.status_code == 200
    models = {m["model"]: m for m in resp.json()["models"]}
    if "users" in models:
        u_caps = models["users"]
        assert u_caps["can_list"] is True
        assert u_caps["can_create"] is False


# ---------------------------------------------------------------------------
# role_permissions is no longer a standalone admin model — managed via permission matrix
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_role_permissions_not_in_admin_registry(
    client: AsyncClient, superadmin: User
):
    resp = await client.get("/api/v1/admin", headers=auth(superadmin))
    assert resp.status_code == 200
    models = [m["model"] for m in resp.json()["models"]]
    assert "role_permissions" not in models
