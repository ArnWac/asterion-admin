import math
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from adminfoundry.admin.registry import admin_site
from adminfoundry.authz.policy_engine import policy_engine
from adminfoundry.database import get_db
from adminfoundry.pagination import paginate
from adminfoundry.dependencies import get_current_user, require_superadmin
from adminfoundry.extensions.jobs.models import Job, JobStatus
from adminfoundry.extensions.jobs.schemas import (
    BulkActionRequest,
    ExportResult,
    ImportRequest,
    ImportResult,
    JobRead,
)
from adminfoundry.extensions.import_export.service import import_export_service
from adminfoundry.extensions.jobs.service import job_service
from adminfoundry.models.user import User

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


def _get_admin_or_404(model_name: str):
    model_admin = admin_site.get(model_name)
    if model_admin is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model '{model_name}' not registered",
        )
    return model_admin


@router.get("")
async def list_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    stmt = select(Job).order_by(Job.created_at.desc())
    jobs, total, pages = await paginate(db, stmt, page, page_size)
    return {
        "items": [JobRead.model_validate(j).model_dump() for j in jobs],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


@router.get("/{job_id}", response_model=JobRead)
async def get_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = await job_service.get_by_id(db, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if not current_user.is_superadmin and str(job.initiator_id) != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return JobRead.model_validate(job)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

@router.post("/admin/{model_name}/export", response_model=ExportResult)
async def export_model(
    model_name: str,
    request: Request,
    q: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Export model rows as JSON — honors permissions, field visibility, and tenant scope."""
    model_admin = _get_admin_or_404(model_name)
    token_payload = getattr(request.state, "token_payload", {})

    job = await job_service.create_job(
        db, job_type="export", initiator_id=current_user.id, model_name=model_name,
    )

    try:
        data = await import_export_service.run_export(
            db, model_admin, current_user, token_payload, q=q,
        )
        job = await job_service.update_status(
            db, job, JobStatus.completed, progress=100,
            result_summary=f"Exported {len(data)} rows",
            output_data={"row_count": len(data)},
        )
        return ExportResult(job_id=job.id, status=job.status, row_count=len(data), data=data)
    except Exception as exc:
        await job_service.update_status(db, job, JobStatus.failed, failure_summary=str(exc))
        raise


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

@router.post("/admin/{model_name}/import", response_model=ImportResult)
async def import_model(
    model_name: str,
    body: ImportRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Import rows. Set dry_run=true to validate without committing."""
    model_admin = _get_admin_or_404(model_name)
    token_payload = getattr(request.state, "token_payload", {})

    job = None
    if not body.dry_run:
        job = await job_service.create_job(
            db, job_type="import", initiator_id=current_user.id,
            model_name=model_name, idempotency_key=body.idempotency_key,
        )
        if job.status == JobStatus.completed:
            return ImportResult(
                dry_run=False, total=0, success_count=0,
                error_count=0, rows=[], job_id=job.id,
            )

    result = await import_export_service.run_import(
        db, model_admin, body.rows, body.dry_run, current_user, token_payload,
        job_id=job.id if job else None,
    )

    if job is not None:
        final = JobStatus.completed if result.error_count == 0 else JobStatus.failed
        await job_service.update_status(
            db, job, final, progress=100,
            result_summary=f"Imported {result.success_count}/{result.total} rows",
            failure_summary=f"{result.error_count} rows failed" if result.error_count else None,
        )
        result.job_id = job.id

    return result


# ---------------------------------------------------------------------------
# Bulk action
# ---------------------------------------------------------------------------

@router.post("/admin/{model_name}/bulk")
async def bulk_action(
    model_name: str,
    body: BulkActionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Execute a declared bulk action on a set of objects."""
    model_admin = _get_admin_or_404(model_name)
    token_payload = getattr(request.state, "token_payload", {})

    from adminfoundry.admin.actions import AdminAction as _AdminAction

    def _action_name(a):
        return a.name if isinstance(a, _AdminAction) else a.get("name")

    def _action_attr(a, key, default=None):
        return getattr(a, key, None) if isinstance(a, _AdminAction) else a.get(key, default)

    action_def = next(
        (a for a in (model_admin.actions or []) if _action_name(a) == body.action), None,
    )
    if action_def is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Action '{body.action}' not defined on '{model_name}'",
        )
    if _action_attr(action_def, "confirm", False) and not body.confirm:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Action '{body.action}' requires confirm=true",
        )
    if not _action_attr(action_def, "bulk", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Action '{body.action}' does not support bulk execution",
        )
    if not policy_engine.can_perform_action(current_user, model_admin, body.action, token_payload):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Action not permitted")

    job = await job_service.create_job(
        db, job_type="bulk_action", initiator_id=current_user.id,
        model_name=model_name, action_name=body.action,
        idempotency_key=body.idempotency_key,
        input_data={"object_ids": [str(oid) for oid in body.object_ids]},
    )

    if job.status == JobStatus.completed:
        return {
            "job_id": str(job.id), "status": job.status,
            "message": "Duplicate submission — existing job returned",
        }

    objects = (
        await db.execute(
            select(model_admin.model).where(model_admin.model.id.in_(body.object_ids))
        )
    ).scalars().all()

    result_summary = f"Bulk '{body.action}' applied to {len(objects)} objects"
    try:
        if isinstance(action_def, _AdminAction):
            result = await action_def.execute(objects, db, current_user)
            result_summary = result.get("summary", result_summary)
    except Exception as exc:
        await job_service.update_status(db, job, JobStatus.failed, failure_summary=str(exc))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

    job = await job_service.update_status(
        db, job, JobStatus.completed, progress=100,
        result_summary=result_summary,
    )

    return {
        "job_id": str(job.id),
        "status": job.status,
        "affected": len(objects),
        "action": body.action,
    }
