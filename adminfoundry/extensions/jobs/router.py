import math
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from adminfoundry.admin.filter_builder import filter_builder
from adminfoundry.admin.registry import admin_site
from adminfoundry.authz.policy_engine import policy_engine
from adminfoundry.database import get_db
from adminfoundry.pagination import paginate
from adminfoundry.dependencies import get_current_user, require_superadmin
from adminfoundry.extensions.jobs.models import Job, JobStatus
from adminfoundry.extensions.jobs.schemas import (
    BulkActionRequest,
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
# Export — file download (CSV / JSON / XLSX) with field-policy enforcement
# ---------------------------------------------------------------------------

@router.get("/admin/{model_name}/export")
async def export_model(
    model_name: str,
    request: Request,
    format: str = Query("csv", pattern="^(csv|json|xlsx)$"),
    q: str | None = Query(None),
    order_by: str | None = Query(None),
    tz: str | None = Query(None, description="IANA timezone, e.g. Europe/Berlin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Export matching records as CSV, JSON or XLSX.

    Field-level policies (field_policies / view_roles) are enforced — hidden fields
    are omitted from the output. Creates a Job record for audit tracking.
    """
    import csv
    import io
    import json as _json
    import sqlalchemy.types as _sa_types
    from datetime import datetime, timezone as _tz_utc
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    from fastapi.responses import Response
    from adminfoundry.admin.router import _tenant_filter, _check_model_access

    model_admin = _get_admin_or_404(model_name)
    token_payload = getattr(request.state, "token_payload", {})
    _check_model_access(model_admin, current_user, token_payload, tenant=getattr(request.state, "tenant", None))

    _target_zone = None
    if tz:
        try:
            _target_zone = ZoneInfo(tz)
        except ZoneInfoNotFoundError:
            pass

    def _apply_tz(value):
        if not isinstance(value, datetime):
            return value
        if value.tzinfo is None:
            value = value.replace(tzinfo=_tz_utc.utc)
        if _target_zone:
            value = value.astimezone(_target_zone)
        return value.strftime("%Y-%m-%d %H:%M:%S")

    def _is_dt_col(col) -> bool:
        return isinstance(col.type, (_sa_types.DateTime, _sa_types.DATETIME, _sa_types.TIMESTAMP))

    # Build query with full filtering
    stmt = select(model_admin.model)
    tf = _tenant_filter(request, model_admin)
    if tf is not None:
        stmt = stmt.where(tf)
    rf = policy_engine.get_record_filter(current_user, model_admin, token_payload)
    if rf is not None:
        stmt = stmt.where(rf)
    search = filter_builder.build_search(model_admin, q)
    if search is not None:
        stmt = stmt.where(search)
    for f in filter_builder.build_filters(model_admin, dict(request.query_params)):
        stmt = stmt.where(f)
    ordering = filter_builder.build_ordering(model_admin, order_by)
    if ordering is not None:
        stmt = stmt.order_by(ordering)
    stmt = stmt.limit(10_000)

    items = (await db.execute(stmt)).scalars().all()

    # Determine visible columns — apply field-level policy
    excluded = model_admin.all_protected
    visible_cols: list[tuple[str, str]] = []
    for col in model_admin.model.__table__.columns:
        if col.name in excluded:
            continue
        fp = policy_engine.evaluate_field(current_user, model_admin, col.name, token_payload)
        if not fp.can_view:
            continue
        header = f"{col.name} ({tz})" if tz and _is_dt_col(col) else col.name
        visible_cols.append((col.name, header))

    def _serialize(obj) -> dict:
        return {header: _apply_tz(getattr(obj, name)) for name, header in visible_cols}

    rows = [_serialize(obj) for obj in items]

    # Track as a job record
    job = await job_service.create_job(
        db, job_type="export", initiator_id=current_user.id, model_name=model_name,
    )
    await job_service.update_status(
        db, job, JobStatus.completed, progress=100,
        result_summary=f"Exported {len(rows)} rows as {format.upper()}",
        output_data={"row_count": len(rows), "format": format},
    )

    tz_label = (tz or "UTC").replace("/", "-")
    filename = f"{model_name}_export_{tz_label}"

    if format == "json":
        content = _json.dumps(rows, default=str, indent=2)
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}.json"'},
        )

    if format == "xlsx":
        try:
            import openpyxl
        except ImportError:
            raise HTTPException(status_code=501,
                                detail="XLSX export requires openpyxl: pip install adminfoundry[xlsx]")
        wb = openpyxl.Workbook()
        ws = wb.active
        if rows:
            ws.append(list(rows[0].keys()))
            for row in rows:
                ws.append([str(v) if v is not None else "" for v in row.values()])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return Response(
            content=buf.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}.xlsx"'},
        )

    # CSV (default)
    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if v is None else str(v)) for k, v in row.items()})
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
    )


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
