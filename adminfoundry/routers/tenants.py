import math
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text
from adminfoundry.database import get_db, get_or_create_tenant_engine
from adminfoundry.dependencies import require_superadmin
from adminfoundry.models.tenant import Tenant
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
    total = (await db.execute(select(func.count()).select_from(Tenant))).scalar_one()
    offset = (page - 1) * page_size
    result = await db.execute(select(Tenant).offset(offset).limit(page_size))
    items = result.scalars().all()
    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total else 0,
    )


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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin),
):
    """Issue a short-lived impersonation token for a target user within a tenant."""
    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    target = (
        await db.execute(select(User).where(User.id == body.target_user_id))
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target user not found")

    token, jti = create_impersonation_token(str(target.id), str(current_user.id))

    log = ImpersonationLog(
        superadmin_id=current_user.id,
        target_user_id=target.id,
        tenant_id=tenant.id,
        jti=jti,
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)

    return ImpersonateResponse(access_token=token, impersonation_log_id=log.id)


@router.post("/{tenant_id}/impersonate/revoke", status_code=status.HTTP_200_OK)
async def revoke_impersonation(
    tenant_id: uuid.UUID,
    body: RevokeImpersonationRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superadmin),
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
    blacklist_token(body.jti, far_future.timestamp())

    log.revoked_at = datetime.now(timezone.utc)
    await db.commit()

    return {"status": "revoked", "jti": body.jti}
