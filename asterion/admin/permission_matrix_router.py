"""Permission-matrix HTTP surface (Roadmap 5.2).

Closes a known UX gap: tenant operators today have to navigate three
admin tables (``tenant_roles`` x ``permission_catalog`` x
``tenant_role_permissions``) to grant or revoke a single permission.
This router collapses the relationship into a single round-trip the
UI can render as a roles x permissions grid.

Endpoints
---------

``GET /api/v1/admin/_permission_matrix``
    Returns the three slices the UI needs together::

        {
          "roles": [{id, name, description, is_system}, ...],
          "permissions": [{key, category}, ...],   # from PermissionCatalog
          "assignments": {role_id: [permission_key, ...], ...}
        }

    Permission keys are sorted by category then key; roles are sorted
    by name. Both lists are stable so the UI can diff a saved view
    against a fresh load.

``PUT /api/v1/admin/_permission_matrix``
    Body: ``{"assignments": {role_id: [permission_keys]}}``. Computes
    the diff against current state and inserts / deletes only what
    actually changed — system roles and unknown roles are rejected.
    Returns the resulting assignments dict.

Authorization
-------------

Both endpoints share the admin auth chain (``require_admin_context``).
GET requires ``admin.tenant_roles.list``; PUT requires
``admin.tenant_role_permissions.update``. System roles are never
mutated regardless of permissions.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asterion.admin.context import AdminContext, require_admin_context
from asterion.authz.permissions import assert_permission
from asterion.db.dependencies import get_async_session
from asterion.models.permission_catalog import PermissionCatalog
from asterion.models.tenant_rbac import TenantRole, TenantRolePermission
from asterion.security.validation import (
    InvalidPermissionKeyError,
    validate_permission_key,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Wire shapes
# ---------------------------------------------------------------------------


class _PermissionMatrixUpdate(BaseModel):
    """Wire input for PUT — one entry per role to update.

    Roles missing from the dict are left untouched (so the UI can
    submit a partial diff). Unknown permission keys are rejected:
    the catalog is the source of truth and silently dropping
    keys would mask typos.
    """

    assignments: dict[str, list[str]] = Field(
        ...,
        description=(
            "Mapping of role UUID (string) → desired permission key list. "
            "Roles not present in the dict keep their current assignments."
        ),
    )


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------


@router.get("/_permission_matrix")
async def get_matrix(
    ctx: AdminContext = Depends(require_admin_context),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    assert_permission(ctx.permissions, "admin.tenant_roles.list")

    roles = (await session.execute(select(TenantRole).order_by(TenantRole.name))).scalars().all()

    permissions = (
        (
            await session.execute(
                select(PermissionCatalog).order_by(
                    PermissionCatalog.category, PermissionCatalog.key
                )
            )
        )
        .scalars()
        .all()
    )

    assignments_rows = (
        await session.execute(
            select(
                TenantRolePermission.role_id,
                TenantRolePermission.permission_key,
            )
        )
    ).all()

    assignments: dict[str, list[str]] = {str(r.id): [] for r in roles}
    for role_id, permission_key in assignments_rows:
        assignments.setdefault(str(role_id), []).append(permission_key)
    # Sort each role's keys so the UI sees a stable order on reload.
    for keys in assignments.values():
        keys.sort()

    return {
        "roles": [
            {
                "id": str(r.id),
                "name": r.name,
                "description": r.description,
                "is_system": r.is_system,
            }
            for r in roles
        ],
        "permissions": [{"key": p.key, "category": p.category} for p in permissions],
        "assignments": assignments,
    }


# ---------------------------------------------------------------------------
# PUT
# ---------------------------------------------------------------------------


def _parse_role_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except (ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role_id: {raw!r}",
        ) from exc


@router.put("/_permission_matrix")
async def update_matrix(
    body: _PermissionMatrixUpdate,
    request: Request,
    ctx: AdminContext = Depends(require_admin_context),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    assert_permission(ctx.permissions, "admin.tenant_role_permissions.update")

    if not body.assignments:
        # No-op: return current state without touching the DB.
        return await get_matrix(ctx=ctx, session=session)

    # Resolve each role id, refusing system roles + unknown ids up
    # front so the response is all-or-nothing — partial application
    # would leave operators guessing which half landed.
    role_ids = [_parse_role_id(rid) for rid in body.assignments.keys()]
    roles_by_id = {
        r.id: r
        for r in (await session.execute(select(TenantRole).where(TenantRole.id.in_(role_ids))))
        .scalars()
        .all()
    }
    for rid in role_ids:
        role = roles_by_id.get(rid)
        if role is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown role_id: {rid}",
            )
        if role.is_system:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Role {role.name!r} is a system role and cannot be edited via the matrix."
                ),
            )

    # Validate every key against the catalog so a typo or stale
    # client-side cache fails loud.
    catalog_keys = {
        row for row in (await session.execute(select(PermissionCatalog.key))).scalars().all()
    }
    requested_keys: set[str] = set()
    for keys in body.assignments.values():
        for k in keys:
            try:
                requested_keys.add(validate_permission_key(k))
            except InvalidPermissionKeyError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid permission key: {k!r}: {exc}",
                ) from exc
    unknown_keys = requested_keys - catalog_keys
    if unknown_keys:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Unknown permission keys (not in PermissionCatalog): "
                + ", ".join(sorted(unknown_keys))
            ),
        )

    # Diff against current state.
    current_by_role: dict[uuid.UUID, set[str]] = {}
    for role_id, perm_key in (
        await session.execute(
            select(
                TenantRolePermission.role_id,
                TenantRolePermission.permission_key,
            ).where(TenantRolePermission.role_id.in_(role_ids))
        )
    ).all():
        current_by_role.setdefault(role_id, set()).add(perm_key)

    for raw_rid, desired_list in body.assignments.items():
        rid = uuid.UUID(raw_rid)
        desired = {validate_permission_key(k) for k in desired_list}
        current = current_by_role.get(rid, set())
        to_add = desired - current
        to_remove = current - desired

        for key in to_add:
            session.add(TenantRolePermission(role_id=rid, permission_key=key))

        if to_remove:
            existing_rows = (
                (
                    await session.execute(
                        select(TenantRolePermission).where(
                            TenantRolePermission.role_id == rid,
                            TenantRolePermission.permission_key.in_(to_remove),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for row in existing_rows:
                await session.delete(row)

    await session.flush()
    return await get_matrix(ctx=ctx, session=session)
