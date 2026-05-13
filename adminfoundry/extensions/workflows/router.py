import math
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from adminfoundry.database import get_db
from adminfoundry.dependencies import get_current_user, require_superadmin
from adminfoundry.extensions.workflows.models import ChangeRequest
from adminfoundry.extensions.workflows.schemas import (
    ChangeRequestCreate,
    ChangeRequestRead,
    ReviewRequest,
    RevertRequest,
)
from adminfoundry.extensions.workflows.service import workflow_service
from adminfoundry.models.user import User

router = APIRouter(prefix="/api/v1/workflow", tags=["workflow"])


@router.get("/change-requests")
async def list_change_requests(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    stmt = select(ChangeRequest).order_by(ChangeRequest.created_at.desc())
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    offset = (page - 1) * page_size
    items = (await db.execute(stmt.offset(offset).limit(page_size))).scalars().all()
    return {
        "items": [ChangeRequestRead.model_validate(cr).model_dump() for cr in items],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": math.ceil(total / page_size) if total else 0,
    }


@router.post("/change-requests", status_code=status.HTTP_201_CREATED, response_model=ChangeRequestRead)
async def submit_change_request(
    body: ChangeRequestCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    tenant = getattr(request.state, "tenant", None)
    tenant_id = getattr(tenant, "id", None)

    cr = await workflow_service.submit_change_request(
        db,
        model_name=body.model_name,
        operation=body.operation,
        requester_id=current_user.id,
        proposed_data=body.proposed_data,
        object_id=body.object_id,
        reason=body.reason,
        tenant_id=tenant_id,
    )
    return ChangeRequestRead.model_validate(cr)


@router.get("/change-requests/{cr_id}", response_model=ChangeRequestRead)
async def get_change_request(
    cr_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(ChangeRequest).where(ChangeRequest.id == cr_id))
    cr = result.scalar_one_or_none()
    if cr is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Change request not found")
    if not current_user.is_superadmin and str(cr.requester_id) != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return ChangeRequestRead.model_validate(cr)


@router.post("/change-requests/{cr_id}/review", response_model=ChangeRequestRead)
async def review_change_request(
    cr_id: uuid.UUID,
    body: ReviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin),
):
    try:
        cr = await workflow_service.review(
            db, cr_id, reviewer_id=current_user.id,
            action=body.action, reason=body.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return ChangeRequestRead.model_validate(cr)


@router.post("/change-requests/{cr_id}/revert", response_model=ChangeRequestRead)
async def revert_change_request(
    cr_id: uuid.UUID,
    body: RevertRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_superadmin),
):
    try:
        cr = await workflow_service.revert(
            db, cr_id, reverter_id=current_user.id, reason=body.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return ChangeRequestRead.model_validate(cr)
