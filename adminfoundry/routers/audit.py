import math
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from adminfoundry.database import get_db
from adminfoundry.dependencies import require_superadmin
from adminfoundry.models.audit_log import AuditLog
from adminfoundry.models.user import User
from adminfoundry.schemas.audit import AuditLogPublic
from adminfoundry.schemas.common import PaginatedResponse

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.get("", response_model=PaginatedResponse[AuditLogPublic])
async def list_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    object_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    base = select(AuditLog)
    count_q = select(func.count()).select_from(AuditLog)
    if object_id:
        base = base.where(AuditLog.object_id == object_id)
        count_q = count_q.where(AuditLog.object_id == object_id)
        base = base.where(AuditLog.action.in_(["created", "updated", "deleted"]))
        count_q = count_q.where(AuditLog.action.in_(["created", "updated", "deleted"]))

    total = (await db.execute(count_q)).scalar_one()
    offset = (page - 1) * page_size
    items = (
        await db.execute(
            base.order_by(AuditLog.created_at.desc()).offset(offset).limit(page_size)
        )
    ).scalars().all()
    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total else 0,
    )
