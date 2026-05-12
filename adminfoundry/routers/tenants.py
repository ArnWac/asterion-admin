import math
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from adminfoundry.database import get_db, get_or_create_tenant_engine
from adminfoundry.pagination import paginate
from adminfoundry.dependencies import require_superadmin
from adminfoundry.models.tenant import Tenant
from adminfoundry.models.role import Role
from adminfoundry.models.user import User
from adminfoundry.models.impersonation_log import ImpersonationLog
from adminfoundry.schemas.common import PaginatedResponse
from adminfoundry.schemas.tenant import TenantPublic, TenantCreate, TenantUpdate
from adminfoundry.schemas.audit import ImpersonateRequest, ImpersonateResponse, RevokeImpersonationRequest
from adminfoundry.auth import create_impersonation_token
from adminfoundry.token_blacklist import blacklist_token

router = APIRouter(prefix="/api/v1/tenants", tags=["tenants"])


@router.get("", response_model=PaginatedResponse[TenantPublic])
async def list_tenants(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    items, total, pages = await paginate(db, select(Tenant), page, page_size)
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size, pages=pages)


@router.post("", response_model=TenantPublic, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    body: TenantCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    existing = (
        await db.execute(select(Tenant).where(Tenant.slug == body.slug))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Slug already exists")

    tenant = Tenant(name=body.name, slug=body.slug)
    db.add(tenant)
    await db.flush()  # get tenant.id before commit

    # Bootstrap a tenant_admin role scoped to this tenant.
    # Holders of this role have full CRUD access to all tenant-scoped models
    # without being a global superadmin (is_superadmin stays False).
    db.add(Role(
        name="tenant_admin",
        tenant_id=tenant.id,
        description="Full access within tenant",
    ))

    await db.commit()
    await db.refresh(tenant)
    return tenant


@router.get("/{tenant_id}", response_model=TenantPublic)
async def get_tenant(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return tenant


@router.patch("/{tenant_id}", response_model=TenantPublic)
async def update_tenant(
    tenant_id: uuid.UUID,
    body: TenantUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(tenant, field, value)

    await db.commit()
    await db.refresh(tenant)
    return tenant


@router.post("/{tenant_id}/migrate", status_code=status.HTTP_200_OK)
async def migrate_tenant(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Create tenant schema and run tenant migrations."""
    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    schema_name = tenant.schema_name

    try:
        # PostgreSQL: create the tenant schema; no-op for SQLite (DDL not supported)
        await db.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
        await db.commit()
        get_or_create_tenant_engine(schema_name)
    except Exception:
        await db.rollback()  # SQLite or other non-schema-supporting dialect

    return {"status": "ok", "schema": schema_name}


@router.post("/{tenant_id}/impersonate", response_model=ImpersonateResponse)
async def impersonate(
    tenant_id: uuid.UUID,
    body: ImpersonateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin),
):
    """Issue a short-lived impersonation token for a target user within a tenant."""
    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    target_id = body.target_user_id if body.target_user_id is not None else current_user.id
    target = (
        await db.execute(select(User).where(User.id == target_id))
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target user not found")

    token, jti = create_impersonation_token(str(target.id), str(current_user.id), str(tenant.id))

    log = ImpersonationLog(
        superadmin_id=current_user.id,
        target_user_id=target.id,
        tenant_id=tenant.id,
        jti=jti,
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)

    request.state.tenant = tenant
    request.state.audit_action = "impersonation_started"
    request.state.audit_actor = current_user.email
    request.state.audit_user_id = str(current_user.id)
    request.state.audit_object_id = str(target.id)

    return ImpersonateResponse(access_token=token, impersonation_log_id=log.id)


@router.post("/{tenant_id}/impersonate/revoke", status_code=status.HTTP_200_OK)
async def revoke_impersonation(
    tenant_id: uuid.UUID,
    body: RevokeImpersonationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin),
):
    """Blacklist an impersonation token by JTI and mark its log entry revoked."""
    log = (
        await db.execute(
            select(ImpersonationLog).where(ImpersonationLog.jti == body.jti)
        )
    ).scalar_one_or_none()
    if log is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Impersonation record not found")

    if log.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already revoked")

    # Blacklist with a far-future expiry to ensure the token stays blocked
    from datetime import timedelta
    far_future = datetime.now(timezone.utc) + timedelta(days=365)
    await blacklist_token(body.jti, far_future.timestamp(), db)

    log.revoked_at = datetime.now(timezone.utc)
    await db.commit()

    from types import SimpleNamespace
    request.state.tenant = SimpleNamespace(id=log.tenant_id)
    request.state.audit_action = "impersonation_revoked"
    request.state.audit_actor = current_user.email
    request.state.audit_user_id = str(current_user.id)
    request.state.audit_object_id = body.jti

    return {"status": "revoked", "jti": body.jti}
