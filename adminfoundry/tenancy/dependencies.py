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
