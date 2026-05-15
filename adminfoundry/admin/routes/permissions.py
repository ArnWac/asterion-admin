"""Permission matrix endpoints — role CRUD capability management.

These routes must be included BEFORE /{model_name}/{object_id} CRUD routes to prevent
FastAPI from matching "permission-matrix" as a model_name parameter.
"""
import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from adminfoundry.admin._helpers import _require_superadmin_or_impersonating
from adminfoundry.admin.registry import admin_site
from adminfoundry.database import get_db
from adminfoundry.dependencies import get_current_user
from adminfoundry.models.user import User
from adminfoundry.settings import settings

router = APIRouter()


@router.get("/permission-matrix/template")
async def get_permission_matrix_template(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Return the full model list with all permissions false — used by the create-role form."""
    payload = getattr(request.state, "token_payload", {})
    _require_superadmin_or_impersonating(current_user, payload, request)

    is_impersonating = bool(payload.get("impersonated_by"))
    tenant = getattr(request.state, "tenant", None)
    in_tenant_context = is_impersonating or tenant is not None
    if in_tenant_context:
        names = [mn for mn in admin_site.model_names()
                 if getattr(admin_site.get(mn), "tenant_scoped", False)]
    else:
        names = admin_site.model_names()

    return [
        {
            "model_name": mn,
            "label": getattr(admin_site.get(mn), "label_plural", mn),
            "can_list": False,
            "can_create": False,
            "can_update": False,
            "can_delete": False,
        }
        for mn in names
    ]


@router.get("/permission-matrix/{role_id}")
async def get_permission_matrix(
    role_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return CRUD caps for every registered model for this role."""
    payload = getattr(request.state, "token_payload", {})
    _require_superadmin_or_impersonating(current_user, payload, request)

    from sqlalchemy import select as _select
    from adminfoundry.models.role_permission import RolePermission
    rows = (
        await db.execute(
            _select(RolePermission).where(RolePermission.role_id == role_id)
        )
    ).scalars().all()
    perms = {r.model_name: r for r in rows}

    is_impersonating = bool(payload.get("impersonated_by"))
    tenant = getattr(request.state, "tenant", None)
    in_tenant_context = is_impersonating or tenant is not None
    if in_tenant_context:
        names = [mn for mn in admin_site.model_names()
                 if getattr(admin_site.get(mn), "tenant_scoped", False)]
    else:
        names = admin_site.model_names()

    return [
        {
            "model_name": mn,
            "label": getattr(admin_site.get(mn), "label_plural", mn),
            "can_list": perms[mn].can_list if mn in perms else False,
            "can_create": perms[mn].can_create if mn in perms else False,
            "can_update": perms[mn].can_update if mn in perms else False,
            "can_delete": perms[mn].can_delete if mn in perms else False,
        }
        for mn in names
    ]


@router.put("/permission-matrix/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def save_permission_matrix(
    role_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Replace all RolePermission records for this role with the submitted matrix."""
    payload = getattr(request.state, "token_payload", {})
    _require_superadmin_or_impersonating(current_user, payload, request)

    from sqlalchemy import delete as _delete, select as _select
    from adminfoundry.models.role_permission import RolePermission

    body = await request.json()
    ops = ("can_list", "can_create", "can_update", "can_delete")

    old_rows = (await db.execute(
        _select(RolePermission).where(RolePermission.role_id == role_id)
    )).scalars().all()
    old_map = {r.model_name: {op: getattr(r, op) for op in ops} for r in old_rows}

    await db.execute(_delete(RolePermission).where(RolePermission.role_id == role_id))

    injected_tenant_id: uuid.UUID | None = None
    if settings.MULTI_TENANT:
        tenant = getattr(request.state, "tenant", None)
        if tenant is not None:
            injected_tenant_id = tenant.id
        elif payload.get("impersonated_by") and payload.get("tenant_id"):
            try:
                injected_tenant_id = uuid.UUID(payload["tenant_id"])
            except (ValueError, AttributeError):
                pass

    new_map: dict = {}
    for entry in body:
        caps = {op: bool(entry.get(op, False)) for op in ops}
        if any(caps.values()):
            db.add(RolePermission(
                role_id=role_id,
                model_name=entry["model_name"],
                tenant_id=injected_tenant_id,
                **caps,
            ))
            new_map[entry["model_name"]] = caps

    changes: dict = {}
    all_models = set(old_map) | set(new_map)
    for mn in sorted(all_models):
        old_caps = old_map.get(mn, {op: False for op in ops})
        new_caps = new_map.get(mn, {op: False for op in ops})
        if old_caps != new_caps:
            old_label = " ".join(op.replace("can_", "") for op in ops if old_caps.get(op))
            new_label = " ".join(op.replace("can_", "") for op in ops if new_caps.get(op))
            changes[mn] = {"from": old_label or "—", "to": new_label or "—"}

    await db.commit()

    request.state.audit_action = "updated"
    request.state.audit_object_id = str(role_id)
    request.state.audit_actor = current_user.email
    request.state.audit_changes = changes or None
