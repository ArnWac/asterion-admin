"""Tenant member-management HTTP surface.

Closes a gap in the v1 RBAC story: a tenant operator could fully manage the
*roles* in their tenant (permission matrix) but could not onboard a new admin
user themselves — creating a ``User`` + ``TenantMembership`` was only possible
via superadmin (``/root/users``) or the CLI (``asterion tenant create``).
"Who is in the tenant" is framework territory (asterion owns
``TenantMembership`` + the tenant-local roles), so member management lives here
rather than in each embedding app.

Endpoints (all mounted under the admin API prefix, tenant-scoped):

``GET /_members``
    List the current tenant's memberships with each member's email, active
    flag, and assigned tenant-role ids/names.

``POST /_members``
    Add a member by email. Body: ``{email, full_name?, role_ids?}``.
      * email maps to an existing global user → create/reactivate the
        membership (idempotent), assign roles.
      * email maps to no user → create an **inactive, passwordless** global
        user, create the membership, issue a single-use invite token, and hand
        the raw token to the configured :class:`InviteNotifier`. The invitee
        sets a password at ``/auth/password-reset/confirm``, which activates
        the account. Response carries ``"invited": true``.

``PATCH /_members/{membership_id}``
    Body: ``{is_active?, role_ids?}``. Activate/deactivate the membership
    and/or replace its tenant-role set. Only memberships in the caller's
    tenant are addressable (others 404).

``DELETE /_members/{membership_id}``
    Remove the membership and its role links. The global ``User`` is left
    intact (it may belong to other tenants).

Authorization
-------------

All endpoints share the admin auth chain (``require_admin_context``) and a
resolved tenant. Permission keys (seeded onto the default ``owner``/``admin``
roles by bootstrap):

* GET    → ``admin.tenant_members.list``
* POST   → ``admin.tenant_members.create``
* PATCH  → ``admin.tenant_members.update``
* DELETE → ``admin.tenant_members.delete``

Tenant isolation is enforced on every query (membership rows are filtered by
the caller's ``tenant_id``), so a tenant admin can never read or mutate another
tenant's members.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asterion.admin.context import AdminContext, require_admin_context
from asterion.auth.invite import create_invite
from asterion.auth.provisioning import create_passwordless_user, ensure_membership
from asterion.authz.permissions import assert_permission
from asterion.db.dependencies import get_async_session
from asterion.models.tenant_membership import TenantMembership
from asterion.models.tenant_rbac import TenantMembershipRole, TenantRole
from asterion.models.user import User

router = APIRouter()


# ---------------------------------------------------------------------------
# Wire shapes
# ---------------------------------------------------------------------------


class _AddMemberBody(BaseModel):
    email: EmailStr
    full_name: str | None = Field(default=None, max_length=255)
    role_ids: list[str] = Field(
        default_factory=list,
        description="Tenant-role UUIDs (strings) to assign to the new member.",
    )


class _UpdateMemberBody(BaseModel):
    is_active: bool | None = Field(
        default=None,
        description="Activate or deactivate this membership within the tenant.",
    )
    role_ids: list[str] | None = Field(
        default=None,
        description=(
            "If present, REPLACE the membership's tenant-role set with exactly "
            "these UUIDs. Omit to leave roles untouched; [] to clear them."
        ),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_tenant(ctx: AdminContext) -> uuid.UUID:
    """Member management is meaningful only inside a tenant scope."""
    if ctx.tenant is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Member management requires a tenant context.",
        )
    try:
        return uuid.UUID(str(ctx.tenant.id))
    except (ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid tenant id: {ctx.tenant.id!r}",
        ) from exc


def _parse_uuid(raw: str, *, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except (ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {label}: {raw!r}",
        ) from exc


async def _resolve_roles(session: AsyncSession, role_ids: list[str]) -> list[TenantRole]:
    """Validate that every id maps to a tenant-local role; 404 on any miss."""
    if not role_ids:
        return []
    parsed = [_parse_uuid(rid, label="role_id") for rid in role_ids]
    roles = (
        (await session.execute(select(TenantRole).where(TenantRole.id.in_(parsed)))).scalars().all()
    )
    found = {r.id for r in roles}
    missing = [str(rid) for rid in parsed if rid not in found]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown role_id(s): " + ", ".join(missing),
        )
    return list(roles)


async def _get_membership(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
) -> TenantMembership:
    """Fetch a membership scoped to the caller's tenant (404 otherwise).

    Filtering by ``tenant_id`` here is the tenant-isolation guard: a
    membership id from another tenant resolves to 404, not 403, so the
    endpoint never confirms the existence of out-of-tenant rows.
    """
    membership = (
        await session.execute(
            select(TenantMembership).where(
                TenantMembership.id == membership_id,
                TenantMembership.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such member in this tenant.",
        )
    return membership


async def _roles_by_membership(
    session: AsyncSession,
    membership_ids: list[uuid.UUID],
) -> dict[uuid.UUID, list[dict[str, str]]]:
    """Map membership id → [{id, name}] for its assigned tenant roles."""
    if not membership_ids:
        return {}
    rows = (
        await session.execute(
            select(
                TenantMembershipRole.membership_id,
                TenantRole.id,
                TenantRole.name,
            )
            .join(TenantRole, TenantRole.id == TenantMembershipRole.role_id)
            .where(TenantMembershipRole.membership_id.in_(membership_ids))
        )
    ).all()
    out: dict[uuid.UUID, list[dict[str, str]]] = {}
    for membership_id, role_id, role_name in rows:
        out.setdefault(membership_id, []).append({"id": str(role_id), "name": role_name})
    for roles in out.values():
        roles.sort(key=lambda r: r["name"])
    return out


def _member_view(
    membership: TenantMembership,
    user: User,
    roles: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "membership_id": str(membership.id),
        "user_id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "is_active": membership.is_active,
        "user_is_active": user.is_active,
        "roles": roles,
    }


async def _set_membership_roles(
    session: AsyncSession,
    *,
    membership_id: uuid.UUID,
    desired_roles: list[TenantRole],
) -> None:
    """Replace a membership's role links with exactly ``desired_roles``."""
    desired = {r.id for r in desired_roles}
    existing_rows = (
        (
            await session.execute(
                select(TenantMembershipRole).where(
                    TenantMembershipRole.membership_id == membership_id
                )
            )
        )
        .scalars()
        .all()
    )
    current = {row.role_id: row for row in existing_rows}

    for role_id in desired - set(current):
        session.add(TenantMembershipRole(membership_id=membership_id, role_id=role_id))
    for role_id in set(current) - desired:
        await session.delete(current[role_id])


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------


@router.get("/_members")
async def list_members(
    ctx: AdminContext = Depends(require_admin_context),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    assert_permission(ctx.permissions, "admin.tenant_members.list")
    tenant_id = _require_tenant(ctx)

    rows = (
        await session.execute(
            select(TenantMembership, User)
            .join(User, User.id == TenantMembership.user_id)
            .where(TenantMembership.tenant_id == tenant_id)
            .order_by(User.email)
        )
    ).all()

    roles_map = await _roles_by_membership(session, [m.id for m, _ in rows])
    return {"members": [_member_view(m, u, roles_map.get(m.id, [])) for m, u in rows]}


# ---------------------------------------------------------------------------
# POST — add / invite
# ---------------------------------------------------------------------------


@router.post("/_members", status_code=status.HTTP_201_CREATED)
async def add_member(
    body: _AddMemberBody,
    request: Request,
    ctx: AdminContext = Depends(require_admin_context),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    assert_permission(ctx.permissions, "admin.tenant_members.create")
    tenant_id = _require_tenant(ctx)

    email = body.email.lower().strip()
    desired_roles = await _resolve_roles(session, body.role_ids)

    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()

    invited = False
    if user is None:
        # No global account yet → create an inactive, passwordless user and
        # issue an invite token. The account has no usable password; the
        # invitee sets one via /auth/password-reset/confirm, which also
        # activates it.
        user = await create_passwordless_user(
            session, email=email, full_name=body.full_name, is_active=False
        )
        invited = True

    # Create or reactivate the membership (idempotent on (user, tenant)).
    membership = await ensure_membership(session, user_id=user.id, tenant_id=tenant_id)

    if desired_roles:
        await _set_membership_roles(
            session, membership_id=membership.id, desired_roles=desired_roles
        )

    await session.flush()

    if invited:
        runtime = request.app.state.asterion
        raw_token = await create_invite(
            session,
            user=user,
            ttl_minutes=runtime.config.invite_token_expire_minutes,
        )
        notifier = runtime.invite_notifier
        if notifier is not None:
            await notifier.send_invite(
                email=email,
                token=raw_token,
                tenant_slug=getattr(ctx.tenant, "slug", None),
                request=request,
            )

    roles_map = await _roles_by_membership(session, [membership.id])
    return {
        "invited": invited,
        "member": _member_view(membership, user, roles_map.get(membership.id, [])),
    }


# ---------------------------------------------------------------------------
# PATCH — set active / roles
# ---------------------------------------------------------------------------


@router.patch("/_members/{membership_id}")
async def update_member(
    membership_id: str,
    body: _UpdateMemberBody,
    ctx: AdminContext = Depends(require_admin_context),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    assert_permission(ctx.permissions, "admin.tenant_members.update")
    tenant_id = _require_tenant(ctx)
    mid = _parse_uuid(membership_id, label="membership_id")

    membership = await _get_membership(session, tenant_id=tenant_id, membership_id=mid)

    if body.is_active is not None:
        membership.is_active = body.is_active

    if body.role_ids is not None:
        desired_roles = await _resolve_roles(session, body.role_ids)
        await _set_membership_roles(
            session, membership_id=membership.id, desired_roles=desired_roles
        )

    await session.flush()

    user = await session.get(User, membership.user_id)
    roles_map = await _roles_by_membership(session, [membership.id])
    return {"member": _member_view(membership, user, roles_map.get(membership.id, []))}


# ---------------------------------------------------------------------------
# DELETE — remove membership
# ---------------------------------------------------------------------------


@router.delete("/_members/{membership_id}", status_code=status.HTTP_200_OK)
async def remove_member(
    membership_id: str,
    ctx: AdminContext = Depends(require_admin_context),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    assert_permission(ctx.permissions, "admin.tenant_members.delete")
    tenant_id = _require_tenant(ctx)
    mid = _parse_uuid(membership_id, label="membership_id")

    membership = await _get_membership(session, tenant_id=tenant_id, membership_id=mid)

    # Drop the tenant-local role links first, then the membership. The global
    # User row is intentionally left intact — it may belong to other tenants.
    role_links = (
        (
            await session.execute(
                select(TenantMembershipRole).where(
                    TenantMembershipRole.membership_id == membership.id
                )
            )
        )
        .scalars()
        .all()
    )
    for link in role_links:
        await session.delete(link)
    await session.delete(membership)
    await session.flush()

    return {"detail": "Member removed from tenant.", "membership_id": membership_id}
