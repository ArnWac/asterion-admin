"""Root tenant admin endpoints — list + read global tenants."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from asterion.auth.dependencies import require_superadmin
from asterion.db.dependencies import get_async_session
from asterion.models.tenant import Tenant
from asterion.models.user import User
from asterion.security.validation import validate_limit_offset

router = APIRouter()


class TenantOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    schema_name: str
    is_active: bool

    @classmethod
    def from_orm_tenant(cls, tenant: Tenant) -> TenantOut:
        return cls(
            id=tenant.id,
            slug=tenant.slug,
            name=tenant.name,
            schema_name=tenant.schema_name,
            is_active=tenant.is_active,
        )


class TenantListResponse(BaseModel):
    items: list[TenantOut]
    total: int
    limit: int
    offset: int


@router.get("/tenants", response_model=TenantListResponse)
async def list_tenants(
    limit: int = 100,
    offset: int = 0,
    search: str | None = None,
    session: AsyncSession = Depends(get_async_session),
    _current: User = Depends(require_superadmin),
) -> TenantListResponse:
    normalized_limit, normalized_offset = validate_limit_offset(limit=limit, offset=offset)

    base = select(Tenant)
    if search:
        needle = f"%{search.strip()}%"
        base = base.where(or_(Tenant.slug.ilike(needle), Tenant.name.ilike(needle)))

    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()

    rows = (
        (
            await session.execute(
                base.order_by(Tenant.slug).limit(normalized_limit).offset(normalized_offset)
            )
        )
        .scalars()
        .all()
    )

    return TenantListResponse(
        items=[TenantOut.from_orm_tenant(t) for t in rows],
        total=total,
        limit=normalized_limit,
        offset=normalized_offset,
    )


@router.get("/tenants/{tenant_id}", response_model=TenantOut)
async def read_tenant(
    tenant_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    _current: User = Depends(require_superadmin),
) -> TenantOut:
    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found.",
        )
    return TenantOut.from_orm_tenant(tenant)
