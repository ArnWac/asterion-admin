"""Admin CRUD routes — list, create, detail, update, delete, import, bulk-action, upload."""
import csv
import io
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from adminfoundry import signals as _signals
from adminfoundry.admin._helpers import (
    _check_model_access,
    _enforce_method_caps,
    _get_admin_or_404,
    _model_supports_soft_delete,
    _tenant_filter,
    _validate_body,
)
from adminfoundry.admin.contract import build_model_contract, CONTRACT_VERSION
from adminfoundry.admin.filter_builder import filter_builder
from adminfoundry.admin.registry import admin_site
from adminfoundry.admin.schema_builder import schema_builder
from adminfoundry.admin.serializer import serializer
from adminfoundry.authz.policy_engine import policy_engine
from adminfoundry.database import get_db, get_admin_db
from adminfoundry.dependencies import get_current_user, require_superadmin
from adminfoundry.models.user import User
from adminfoundry.pagination import paginate
from adminfoundry.schemas.policy import FieldPolicyMeta, ModelPolicyResponse
from adminfoundry.settings import settings

router = APIRouter()


@router.get("/{model_name}")
async def list_objects(
    model_name: str,
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None),
    order_by: str | None = Query(None),
    trash: bool = Query(False, description="Show only soft-deleted records"),
    db: AsyncSession = Depends(get_admin_db),
    current_user: User = Depends(get_current_user),
):
    model_admin = _get_admin_or_404(model_name)
    payload = getattr(request.state, "token_payload", {})
    _check_model_access(model_admin, current_user, payload, tenant=getattr(request.state, "tenant", None))
    await _enforce_method_caps(model_admin, current_user, payload, "list", db)

    stmt = select(model_admin.model)

    tf = _tenant_filter(request, model_admin)
    if tf is not None:
        stmt = stmt.where(tf)

    if _model_supports_soft_delete(model_admin):
        if trash:
            stmt = stmt.where(model_admin.model.deleted_at.is_not(None))
        else:
            stmt = stmt.where(model_admin.model.deleted_at.is_(None))

    rf = policy_engine.get_record_filter(current_user, model_admin, payload)
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

    items, total, pages = await paginate(db, stmt, page, page_size)
    return {
        "items": serializer.serialize_many(items, model_admin),
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


@router.post("/{model_name}", status_code=status.HTTP_201_CREATED)
async def create_object(
    model_name: str,
    request: Request,
    db: AsyncSession = Depends(get_admin_db),
    current_user: User = Depends(get_current_user),
):
    model_admin = _get_admin_or_404(model_name)
    payload = getattr(request.state, "token_payload", {})
    _check_model_access(model_admin, current_user, payload, tenant=getattr(request.state, "tenant", None))
    await _enforce_method_caps(model_admin, current_user, payload, "create", db)

    create_schema = schema_builder.build_create_schema(model_admin)
    body = await request.json()
    validated = _validate_body(create_schema, body)

    for field_name in validated.model_dump(exclude_none=True):
        fp = policy_engine.evaluate_field(current_user, model_admin, field_name, payload)
        if not fp.can_edit:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Field '{field_name}' is not editable",
            )

    data = model_admin.before_create(validated.model_dump(exclude_none=True))

    if model_admin.tenant_scoped and settings.MULTI_TENANT and hasattr(model_admin.model, "tenant_id"):
        tenant = getattr(request.state, "tenant", None)
        if tenant is not None:
            data.setdefault("tenant_id", str(tenant.id))
        elif payload.get("impersonated_by") and payload.get("tenant_id"):
            data.setdefault("tenant_id", payload["tenant_id"])

    obj = model_admin.model(**data)
    db.add(obj)
    try:
        await db.commit()
        await db.refresh(obj)
    except IntegrityError as exc:
        await db.rollback()
        orig = str(exc.orig)
        if "UNIQUE constraint" in orig or "unique constraint" in orig:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A record with these values already exists",
            )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=orig)
    except OperationalError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {exc.orig}",
        )

    request.state.audit_action = "created"
    request.state.audit_object_id = str(obj.id)
    request.state.audit_actor = current_user.email

    await _signals.emit("post_create", model_name=model_name, obj=obj, user=current_user)
    return serializer.serialize(obj, model_admin)


@router.post("/{model_name}/import")
async def import_objects(
    model_name: str,
    request: Request,
    file: UploadFile = File(...),
    dry_run: bool = Query(True),
    db: AsyncSession = Depends(get_admin_db),
    current_user: User = Depends(get_current_user),
):
    """Import records from a CSV file.

    With dry_run=true (default) validates rows and returns a preview without writing.
    With dry_run=false commits all valid rows; rolls back if any row fails.
    """
    model_admin = _get_admin_or_404(model_name)
    if not getattr(model_admin, "allow_import", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Import not enabled for this model")
    payload = getattr(request.state, "token_payload", {})
    _check_model_access(model_admin, current_user, payload, tenant=getattr(request.state, "tenant", None))
    await _enforce_method_caps(model_admin, current_user, payload, "create", db)

    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File must be UTF-8 encoded CSV")

    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CSV file is empty or has no data rows")

    create_schema = schema_builder.build_create_schema(model_admin)
    preview: list[dict] = []
    errors: list[dict] = []
    imported = 0

    for i, row in enumerate(rows):
        clean = {k: v for k, v in row.items() if v != ""}
        try:
            validated = create_schema.model_validate(clean)
            data = model_admin.before_create(validated.model_dump(exclude_none=True))
            if not dry_run:
                obj = model_admin.model(**data)
                db.add(obj)
                await db.flush()
                imported += 1
            elif len(preview) < 5:
                preview.append({"row": i + 1, "data": clean})
        except Exception as exc:
            errors.append({"row": i + 1, "error": str(exc)[:300], "data": clean})

    if not dry_run:
        if errors:
            await db.rollback()
        else:
            await db.commit()

    return {
        "total": len(rows),
        "imported": imported,
        "errors": errors,
        "dry_run": dry_run,
        "preview": preview,
    }


# ---------------------------------------------------------------------------
# /{model_name}/meta, /lookup, /policy — must appear BEFORE /{model_name}/{object_id}
# so that the literal path segments are matched before FastAPI tries to coerce to UUID.
# ---------------------------------------------------------------------------

@router.get("/{model_name}/meta")
async def model_meta(
    model_name: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Return full field and action contract metadata for a registered model."""
    model_admin = _get_admin_or_404(model_name)
    payload = getattr(request.state, "token_payload", {})
    _check_model_access(model_admin, current_user, payload, tenant=getattr(request.state, "tenant", None))
    return build_model_contract(model_admin, registry=admin_site)


@router.get("/{model_name}/lookup")
async def lookup_objects(
    model_name: str,
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None),
    db: AsyncSession = Depends(get_admin_db),
    current_user: User = Depends(get_current_user),
):
    """Generic async relation-selection lookup — returns lightweight {id, label} items."""
    model_admin = _get_admin_or_404(model_name)
    payload = getattr(request.state, "token_payload", {})
    _check_model_access(model_admin, current_user, payload, tenant=getattr(request.state, "tenant", None))

    stmt = select(model_admin.model)

    tf = _tenant_filter(request, model_admin)
    if tf is not None:
        stmt = stmt.where(tf)

    search = filter_builder.build_search(model_admin, q)
    if search is not None:
        stmt = stmt.where(search)

    items, total, pages = await paginate(db, stmt, page, page_size)

    label_field = (
        model_admin.lookup_field
        or (model_admin.list_display[0] if model_admin.list_display else None)
    )
    result = []
    for obj in items:
        label = str(getattr(obj, label_field, None) or obj.id) if label_field else str(obj.id)
        result.append({"id": str(obj.id), "label": label})

    return {
        "items": result,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


@router.get("/{model_name}/policy")
async def model_policy(
    model_name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return effective field policies and capability flags for the current user."""
    model_admin = _get_admin_or_404(model_name)
    payload = getattr(request.state, "token_payload", {})

    from adminfoundry.admin.contract import build_field_metadata
    from adminfoundry.authz.role_caps import fetch_model_caps
    fields = build_field_metadata(model_admin)
    field_policies = [
        FieldPolicyMeta(
            field=f.name,
            **vars(policy_engine.evaluate_field(current_user, model_admin, f.name, payload)),
        )
        for f in fields
    ]
    db_caps = await fetch_model_caps(current_user, model_name, db)
    in_tenant_context = bool(
        payload.get("impersonated_by") or getattr(request.state, "tenant", None)
    )
    caps = policy_engine.effective_model_caps(
        current_user, model_admin, payload, db_caps=db_caps, in_tenant_context=in_tenant_context
    )
    return ModelPolicyResponse(
        model=model_name,
        contract_version=CONTRACT_VERSION,
        field_policies=field_policies,
        can_list=caps["can_list"],
        can_create=caps["can_create"],
        can_read=caps["can_read"],
        can_update=caps["can_update"],
        can_delete=caps["can_delete"],
    )


@router.post("/{model_name}/bulk-action")
async def bulk_action_direct(
    model_name: str,
    request: Request,
    db: AsyncSession = Depends(get_admin_db),
    current_user: User = Depends(get_current_user),
):
    """Execute a declared bulk action directly — no job queue required."""
    from adminfoundry.admin.actions import AdminAction as _AdminAction

    model_admin = _get_admin_or_404(model_name)
    payload = getattr(request.state, "token_payload", {})
    _check_model_access(model_admin, current_user, payload, tenant=getattr(request.state, "tenant", None))

    body = await request.json()
    action_name: str = body.get("action", "")
    object_ids: list = body.get("object_ids", [])

    def _attr(a, key, default=None):
        return getattr(a, key, None) if isinstance(a, _AdminAction) else a.get(key, default)

    action_def = next(
        (a for a in (model_admin.actions or []) if _attr(a, "name") == action_name), None
    )
    if action_def is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Action '{action_name}' not defined on '{model_name}'")
    if not _attr(action_def, "bulk", False):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Action '{action_name}' does not support bulk execution")

    objects = (
        await db.execute(select(model_admin.model).where(model_admin.model.id.in_(object_ids)))
    ).scalars().all()

    if not isinstance(action_def, _AdminAction):
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED,
                            detail=f"Action '{action_name}' has no execute() implementation — use AdminAction subclass")

    try:
        result = await action_def.execute(objects, db, current_user)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

    request.state.audit_action = "bulk_action"
    request.state.audit_object_id = action_name
    request.state.audit_actor = current_user.email

    return {
        "action": action_name,
        "affected": len(objects),
        "summary": result.get("summary", f"{len(objects)} object(s) updated"),
    }


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_file(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Upload a file to the configured storage backend. Returns {path, url}."""
    from fastapi import UploadFile
    from adminfoundry.storage import storage, generate_path

    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            detail="Multipart form-data required")

    form = await request.form()
    file: UploadFile | None = form.get("file")  # type: ignore[assignment]
    if file is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Field 'file' is required")

    prefix = str(form.get("prefix", ""))
    path = generate_path(file.filename or "upload", prefix)
    saved = await storage.save(path, file.file)
    return {"path": saved, "url": storage.url(saved)}


@router.get("/{model_name}/{object_id}")
async def get_object(
    model_name: str,
    object_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_admin_db),
    current_user: User = Depends(get_current_user),
):
    model_admin = _get_admin_or_404(model_name)
    payload = getattr(request.state, "token_payload", {})
    _check_model_access(model_admin, current_user, payload, tenant=getattr(request.state, "tenant", None))
    await _enforce_method_caps(model_admin, current_user, payload, "read", db)

    obj = (
        await db.execute(select(model_admin.model).where(model_admin.model.id == object_id))
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Object not found")

    if _model_supports_soft_delete(model_admin) and obj.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Object not found")

    rp = policy_engine.check_record_access(current_user, model_admin, obj, payload)
    if not rp.can_read:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access to this record is denied")

    return serializer.serialize(obj, model_admin)


@router.patch("/{model_name}/{object_id}")
async def update_object(
    model_name: str,
    object_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_admin_db),
    current_user: User = Depends(get_current_user),
):
    model_admin = _get_admin_or_404(model_name)
    payload = getattr(request.state, "token_payload", {})
    _check_model_access(model_admin, current_user, payload, tenant=getattr(request.state, "tenant", None))
    await _enforce_method_caps(model_admin, current_user, payload, "update", db)

    update_schema = schema_builder.build_update_schema(model_admin)
    body = await request.json()
    validated = _validate_body(update_schema, body)

    obj = (
        await db.execute(select(model_admin.model).where(model_admin.model.id == object_id))
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Object not found")

    rp = policy_engine.check_record_access(current_user, model_admin, obj, payload)
    if not rp.can_update:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access to this record is denied")

    for field_name in validated.model_dump(exclude_none=True):
        fp = policy_engine.evaluate_field(current_user, model_admin, field_name, payload, record=obj)
        if not fp.can_edit:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Field '{field_name}' is not editable",
            )

    changes = {}
    for field, value in validated.model_dump(exclude_none=True).items():
        old = getattr(obj, field, None)
        if str(old) != str(value):
            changes[field] = {"from": str(old) if old is not None else None, "to": str(value)}
        setattr(obj, field, value)

    await db.commit()
    await db.refresh(obj)

    request.state.audit_action = "updated"
    request.state.audit_object_id = str(object_id)
    request.state.audit_actor = current_user.email
    request.state.audit_changes = changes or None

    await _signals.emit("post_update", model_name=model_name, obj=obj, user=current_user, changes=changes)
    return serializer.serialize(obj, model_admin)


@router.delete("/{model_name}/{object_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_object(
    model_name: str,
    object_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_admin_db),
    current_user: User = Depends(get_current_user),
):
    model_admin = _get_admin_or_404(model_name)
    if not getattr(model_admin, "allow_delete", True):
        raise HTTPException(status_code=status.HTTP_405_METHOD_NOT_ALLOWED, detail="Deletion not allowed for this model")
    payload = getattr(request.state, "token_payload", {})
    _check_model_access(model_admin, current_user, payload, tenant=getattr(request.state, "tenant", None))
    await _enforce_method_caps(model_admin, current_user, payload, "delete", db)

    obj = (
        await db.execute(select(model_admin.model).where(model_admin.model.id == object_id))
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Object not found")

    rp = policy_engine.check_record_access(current_user, model_admin, obj, payload)
    if not rp.can_delete:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access to this record is denied")

    if _model_supports_soft_delete(model_admin):
        from adminfoundry.models.base import utcnow
        obj.deleted_at = utcnow()
        await db.commit()
        request.state.audit_action = "deleted"
        request.state.audit_object_id = str(object_id)
        request.state.audit_actor = current_user.email
        await _signals.emit("post_delete", model_name=model_name, object_id=str(object_id), user=current_user)
        return

    await _signals.emit("pre_delete", model_name=model_name, obj=obj, user=current_user)
    await db.delete(obj)
    await db.commit()

    request.state.audit_action = "deleted"
    request.state.audit_object_id = str(object_id)
    request.state.audit_actor = current_user.email
    await _signals.emit("post_delete", model_name=model_name, object_id=str(object_id), user=current_user)


@router.post("/{model_name}/{object_id}/restore", status_code=status.HTTP_204_NO_CONTENT)
async def restore_object(
    model_name: str,
    object_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_admin_db),
    current_user: User = Depends(get_current_user),
):
    """Restore a soft-deleted record from trash."""
    model_admin = _get_admin_or_404(model_name)
    if not _model_supports_soft_delete(model_admin):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Soft-delete not enabled for this model")
    payload = getattr(request.state, "token_payload", {})
    _check_model_access(model_admin, current_user, payload, tenant=getattr(request.state, "tenant", None))

    obj = (
        await db.execute(
            select(model_admin.model).where(
                model_admin.model.id == object_id,
                model_admin.model.deleted_at.is_not(None),
            )
        )
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Object not found or not in trash")

    obj.deleted_at = None
    await db.commit()

    request.state.audit_action = "restored"
    request.state.audit_object_id = str(object_id)
    request.state.audit_actor = current_user.email


@router.delete("/{model_name}/{object_id}/hard", status_code=status.HTTP_204_NO_CONTENT)
async def hard_delete_object(
    model_name: str,
    object_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_admin_db),
    current_user: User = Depends(require_superadmin),
):
    """Permanently delete a record — bypasses soft-delete. Superadmin only."""
    model_admin = _get_admin_or_404(model_name)
    if not getattr(model_admin, "allow_delete", True):
        raise HTTPException(status_code=status.HTTP_405_METHOD_NOT_ALLOWED, detail="Deletion not allowed for this model")
    payload = getattr(request.state, "token_payload", {})
    _check_model_access(model_admin, current_user, payload, tenant=getattr(request.state, "tenant", None))

    obj = (
        await db.execute(select(model_admin.model).where(model_admin.model.id == object_id))
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Object not found")

    await _signals.emit("pre_delete", model_name=model_name, obj=obj, user=current_user)
    await db.delete(obj)
    await db.commit()

    request.state.audit_action = "hard_deleted"
    request.state.audit_object_id = str(object_id)
    request.state.audit_actor = current_user.email
    await _signals.emit("post_delete", model_name=model_name, object_id=str(object_id), user=current_user)
