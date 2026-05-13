"""
AdminAction tests.

Covers: DeactivateUsersAction.execute(), DisableTenantAction.execute(),
bulk endpoint happy path, missing confirm, unknown action, and execute()
exception → job marked failed.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from examples.default.admin_config import DeactivateUsersAction, DisableTenantAction
from adminfoundry.auth import create_access_token, hash_password
from adminfoundry.models.user import User
from adminfoundry.models.tenant import Tenant

# Mount the jobs router on the shared test app (it's opt-in and not in the default config)
from adminfoundry.extensions.jobs.router import router as _jobs_router
from examples.default.app import app as _app
_already_mounted = any(
    getattr(r, "path", "").startswith("/api/v1/jobs") for r in _app.routes
)
if not _already_mounted:
    _app.include_router(_jobs_router)


def auth(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


async def _make_user(db: AsyncSession, email: str, *, active=True) -> User:
    u = User(
        email=email,
        hashed_password=hash_password("pass"),
        is_active=active,
        is_superadmin=False,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


# ---------------------------------------------------------------------------
# Unit tests — execute() directly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deactivate_action_sets_inactive(db: AsyncSession, superadmin: User):
    target = await _make_user(db, "target@example.com", active=True)
    action = DeactivateUsersAction()
    result = await action.execute([target], db, superadmin)

    assert result["affected"] == 1
    await db.refresh(target)
    assert target.is_active is False


@pytest.mark.asyncio
async def test_deactivate_action_bulk(db: AsyncSession, superadmin: User):
    u1 = await _make_user(db, "u1@example.com")
    u2 = await _make_user(db, "u2@example.com")
    action = DeactivateUsersAction()
    result = await action.execute([u1, u2], db, superadmin)

    assert result["affected"] == 2
    await db.refresh(u1)
    await db.refresh(u2)
    assert u1.is_active is False
    assert u2.is_active is False


@pytest.mark.asyncio
async def test_disable_tenant_action(db: AsyncSession, superadmin: User):
    tenant = Tenant(name="Acme", slug="acme", is_active=True)
    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)

    action = DisableTenantAction()
    result = await action.execute([tenant], db, superadmin)

    assert result["affected"] == 1
    await db.refresh(tenant)
    assert tenant.is_active is False


# ---------------------------------------------------------------------------
# Bulk endpoint — HTTP path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bulk_deactivate_via_endpoint(
    client: AsyncClient, db: AsyncSession, superadmin: User
):
    target = await _make_user(db, "bulk_target@example.com", active=True)

    resp = await client.post(
        "/api/v1/jobs/admin/users/bulk",
        json={
            "action": "deactivate",
            "object_ids": [str(target.id)],
            "confirm": True,
        },
        headers=auth(superadmin),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["affected"] == 1
    assert data["action"] == "deactivate"
    assert data["status"] == "completed"

    await db.refresh(target)
    assert target.is_active is False


@pytest.mark.asyncio
async def test_bulk_unknown_action_returns_400(
    client: AsyncClient, superadmin: User
):
    resp = await client.post(
        "/api/v1/jobs/admin/users/bulk",
        json={"action": "nonexistent", "object_ids": [], "confirm": True},
        headers=auth(superadmin),
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_bulk_missing_confirm_returns_422(
    client: AsyncClient, superadmin: User
):
    resp = await client.post(
        "/api/v1/jobs/admin/users/bulk",
        json={"action": "deactivate", "object_ids": [], "confirm": False},
        headers=auth(superadmin),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_bulk_action_execute_exception_marks_job_failed(
    client: AsyncClient, db: AsyncSession, superadmin: User, monkeypatch
):
    """If execute() raises, the endpoint must mark the job failed and return 500."""
    from examples.default import admin_config as _cfg  # noqa: F401 — ensure registrations loaded

    original_execute = DeactivateUsersAction.execute

    async def _explode(self, objects, _db, _user):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(DeactivateUsersAction, "execute", _explode)

    target = await _make_user(db, "fail_target@example.com")
    resp = await client.post(
        "/api/v1/jobs/admin/users/bulk",
        json={
            "action": "deactivate",
            "object_ids": [str(target.id)],
            "confirm": True,
        },
        headers=auth(superadmin),
    )
    assert resp.status_code == 500

    # Verify job was persisted as failed
    from sqlalchemy import select
    from adminfoundry.extensions.jobs.models import Job, JobStatus
    jobs = (await db.execute(select(Job).where(Job.action_name == "deactivate"))).scalars().all()
    assert any(j.status == JobStatus.failed for j in jobs)
