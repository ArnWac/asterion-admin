"""FastAPI dependencies for tenant-scoped authorization."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from adminfoundry.database import get_db
from adminfoundry.dependencies import get_current_user
from adminfoundry.models.tenant_membership import TenantMembership
from adminfoundry.models.user import User
from adminfoundry.settings import settings
from adminfoundry.tenancy.context import TenantAuthContext
from adminfoundry.tenancy.schema_strategy import get_tenant_session
from adminfoundry.tenancy.tenant_models import TenantMembershipRole, TenantRole


async def require_tenant_membership(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TenantMembership | None:
    """Enforce active tenant membership for every tenant-scoped request.

    - No tenant context (root panel): pass through, return None.
    - Superadmin + impersonation token: token/tenant match is enforced by
      _check_model_access; skip DB membership check, return None.
    - Any other user in tenant context: must have an active TenantMembership,
      else 403.

    Sets request.state.tenant_membership so downstream helpers can read it
    without re-querying.
    """
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        return None  # root panel, no tenant context

    payload = getattr(request.state, "token_payload", {})
    is_impersonating = bool(payload.get("impersonated_by"))
    if current_user.is_superadmin and is_impersonating:
        if tenant is not None:
            token_tenant_id = payload.get("tenant_id")
            if not token_tenant_id or str(token_tenant_id) != str(tenant.id):
                raise HTTPException(
                    status_code=403,
                    detail="Impersonation token is not valid for this tenant",
                )
        return None

    result = await db.execute(
        select(TenantMembership)
        .where(TenantMembership.user_id == current_user.id)
        .where(TenantMembership.tenant_id == tenant.id)
        .where(TenantMembership.is_active == True)  # noqa: E712
        .options(selectinload(TenantMembership.roles))
    )
    membership = result.scalar_one_or_none()

    if membership is None:
        raise HTTPException(status_code=403, detail="You do not have access to this tenant")

    request.state.tenant_membership = membership
    return membership


async def require_tenant_auth_context(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TenantAuthContext | None:
    """Build a TenantAuthContext by verifying public membership then loading
    tenant-local roles from the active tenant schema.

    Returns None for root-panel requests (no tenant) and for superadmin
    impersonation flows where the bypass is handled downstream.
    """
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        return None

    payload = getattr(request.state, "token_payload", {})
    is_impersonating = bool(payload.get("impersonated_by"))
    if current_user.is_superadmin and is_impersonating:
        token_tenant_id = payload.get("tenant_id")
        if not token_tenant_id or str(token_tenant_id) != str(tenant.id):
            raise HTTPException(
                status_code=403,
                detail="Impersonation token is not valid for this tenant",
            )
        return None

    membership = (
        await db.execute(
            select(TenantMembership)
            .where(TenantMembership.user_id == current_user.id)
            .where(TenantMembership.tenant_id == tenant.id)
            .where(TenantMembership.is_active == True)  # noqa: E712
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=403, detail="You do not have access to this tenant")

    roles: list[TenantRole] = []
    permission_keys: set[str] = set()

    if "postgresql" in settings.DATABASE_URL:
        async for tenant_db in get_tenant_session(tenant.schema_name):
            role_rows = (
                await tenant_db.execute(
                    select(TenantRole)
                    .join(TenantMembershipRole, TenantMembershipRole.role_id == TenantRole.id)
                    .where(TenantMembershipRole.membership_id == membership.id)
                    .options(selectinload(TenantRole.permissions))
                )
            ).scalars().all()
            roles = list(role_rows)
            for role in roles:
                for perm in role.permissions:
                    permission_keys.add(perm.permission_key)

    ctx = TenantAuthContext(
        tenant=tenant,
        membership=membership,
        roles=roles,
        permission_keys=permission_keys,
    )
    request.state.tenant_auth = ctx
    request.state.tenant_membership = membership
    return ctx
