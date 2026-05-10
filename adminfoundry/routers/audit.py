from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from adminfoundry.database import get_db
from adminfoundry.dependencies import get_current_user, require_superadmin
from adminfoundry.models.audit_log import AuditLog
from adminfoundry.models.user import User
from adminfoundry.pagination import paginate
from adminfoundry.schemas.audit import AuditLogPublic
from adminfoundry.schemas.common import PaginatedResponse

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.get("", response_model=PaginatedResponse[AuditLogPublic])
async def list_audit_logs(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    object_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin),
):
    request.state.audit_action = "audit_log_viewed"
    request.state.audit_actor = current_user.email
    request.state.audit_user_id = str(current_user.id)

    stmt = select(AuditLog)
    if object_id:
        stmt = stmt.where(
            AuditLog.object_id == object_id,
            AuditLog.action.in_(["created", "updated", "deleted"]),
        )
    stmt = stmt.order_by(AuditLog.created_at.desc())

    items, total, pages = await paginate(db, stmt, page, page_size)
    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )
