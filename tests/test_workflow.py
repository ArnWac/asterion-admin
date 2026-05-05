"""Phase 13 — Workflow, Approvals, and Reversible Admin Changes."""
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from adminfoundry.models.user import User
from adminfoundry.models.role import Role
from adminfoundry.auth import hash_password, create_access_token_with_iat


@pytest_asyncio.fixture
async def admin_token(db: AsyncSession) -> str:
    user = User(
        email="admin13@example.com",
        hashed_password=hash_password("pass"),
        full_name="Admin13",
        is_active=True,
        is_superadmin=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return create_access_token_with_iat(str(user.id))


@pytest.mark.asyncio
async def test_submit_change_request(client: AsyncClient, admin_token: str):
    r = await client.post(
        "/api/v1/workflow/change-requests",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "model_name": "roles",
            "operation": "create",
            "proposed_data": {"name": "reviewer"},
            "reason": "Adding reviewer role",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "pending"
    assert body["model_name"] == "roles"
    assert body["operation"] == "create"


@pytest.mark.asyncio
async def test_list_change_requests(client: AsyncClient, admin_token: str):
    # Create one first
    await client.post(
        "/api/v1/workflow/change-requests",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"model_name": "roles", "operation": "create", "proposed_data": {"name": "x"}},
    )
    r = await client.get(
        "/api/v1/workflow/change-requests",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    assert r.json()["total"] >= 1


@pytest.mark.asyncio
async def test_approve_change_request_applies_it(client: AsyncClient, admin_token: str, db: AsyncSession):
    cr_r = await client.post(
        "/api/v1/workflow/change-requests",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "model_name": "roles",
            "operation": "create",
            "proposed_data": {"name": "approvedRole"},
        },
    )
    cr_id = cr_r.json()["id"]

    review_r = await client.post(
        f"/api/v1/workflow/change-requests/{cr_id}/review",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"action": "approve"},
    )
    assert review_r.status_code == 200
    assert review_r.json()["status"] == "applied"

    # The role should exist now
    from sqlalchemy import select
    role = (await db.execute(select(Role).where(Role.name == "approvedRole"))).scalar_one_or_none()
    assert role is not None


@pytest.mark.asyncio
async def test_reject_change_request_not_applied(client: AsyncClient, admin_token: str, db: AsyncSession):
    cr_r = await client.post(
        "/api/v1/workflow/change-requests",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "model_name": "roles",
            "operation": "create",
            "proposed_data": {"name": "rejectedRole"},
        },
    )
    cr_id = cr_r.json()["id"]

    review_r = await client.post(
        f"/api/v1/workflow/change-requests/{cr_id}/review",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"action": "reject", "reason": "Not needed"},
    )
    assert review_r.status_code == 200
    assert review_r.json()["status"] == "rejected"
    assert review_r.json()["rejection_reason"] == "Not needed"

    # The role must NOT exist
    from sqlalchemy import select
    role = (await db.execute(select(Role).where(Role.name == "rejectedRole"))).scalar_one_or_none()
    assert role is None


@pytest.mark.asyncio
async def test_revert_applied_change(client: AsyncClient, admin_token: str, db: AsyncSession):
    # Create a role, then submit update CR, approve it, then revert
    role = Role(name="original_name")
    db.add(role)
    await db.commit()
    await db.refresh(role)

    cr_r = await client.post(
        "/api/v1/workflow/change-requests",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "model_name": "roles",
            "object_id": str(role.id),
            "operation": "update",
            "proposed_data": {"name": "updated_name"},
        },
    )
    cr_id = cr_r.json()["id"]

    # Approve
    approve_r = await client.post(
        f"/api/v1/workflow/change-requests/{cr_id}/review",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"action": "approve"},
    )
    assert approve_r.json()["status"] == "applied"

    # Revert
    revert_r = await client.post(
        f"/api/v1/workflow/change-requests/{cr_id}/revert",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"reason": "Rolled back"},
    )
    assert revert_r.status_code == 200
    assert revert_r.json()["status"] == "reverted"


@pytest.mark.asyncio
async def test_double_review_rejected(client: AsyncClient, admin_token: str):
    cr_r = await client.post(
        "/api/v1/workflow/change-requests",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"model_name": "roles", "operation": "create", "proposed_data": {"name": "zz"}},
    )
    cr_id = cr_r.json()["id"]

    await client.post(
        f"/api/v1/workflow/change-requests/{cr_id}/review",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"action": "approve"},
    )
    r2 = await client.post(
        f"/api/v1/workflow/change-requests/{cr_id}/review",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"action": "reject"},
    )
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_revert_non_applied_rejected(client: AsyncClient, admin_token: str):
    cr_r = await client.post(
        "/api/v1/workflow/change-requests",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"model_name": "roles", "operation": "create", "proposed_data": {"name": "yyy"}},
    )
    cr_id = cr_r.json()["id"]

    r = await client.post(
        f"/api/v1/workflow/change-requests/{cr_id}/revert",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"reason": "Trying to revert pending"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_workflow_metadata_in_contract(client: AsyncClient, admin_token: str):
    """Model contract exposes requires_approval flag."""
    r = await client.get(
        "/api/v1/admin/roles/meta",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    assert "requires_approval" in r.json()


@pytest.mark.asyncio
async def test_model_without_workflow_unchanged(client: AsyncClient, admin_token: str):
    """Models without requires_approval still behave normally."""
    r = await client.get(
        "/api/v1/admin/users",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
