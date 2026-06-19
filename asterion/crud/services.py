from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asterion.crud.payload import clean_write_payload
from asterion.crud.query import (
    apply_date_hierarchy,
    apply_filters,
    apply_ordering,
    apply_search,
    coerce_primary_key_value,
    count_statement_for,
    normalize_limit_offset,
    primary_key_column,
)
from asterion.crud.types import PageResult
from asterion.registry import ModelAdmin
from asterion.schemas.builder import build_model_schema
from asterion.schemas.fields import AdminModelSchema, FieldInfo
from asterion.schemas.serialization.serializer import (
    serialize_record,
    serialize_record_with_policy,
    serialize_records,
    serialize_records_with_policy,
)

if TYPE_CHECKING:
    from asterion.admin.context import AdminContext


async def _apply_field_policy_to_schema(
    schema: AdminModelSchema,
    admin_class: ModelAdmin,
    ctx: AdminContext | None,
    obj: Any | None,
) -> AdminModelSchema:
    """Re-shape the write schema based on per-field policy decisions.

    * :data:`FieldPermission.HIDDEN` → field is dropped (treated as
      unknown, payload entries for it are rejected by
      ``clean_write_payload``).
    * :data:`FieldPermission.READ` → field is kept but marked
      ``read_only`` so writes are rejected by ``clean_write_payload``.
    * :data:`FieldPermission.WRITE` → unchanged.

    Returns the original schema when there's no ctx or no policy —
    the cheap path for legacy callers / public APIs.

    Roadmap 2.1 note: this is the ``strictest`` combination applied to
    the write path. The incoming ``schema`` already carries the *static*
    field class — ``build_model_schema`` dropped ``protected_fields``
    and flagged ``readonly_fields`` / auto columns as ``read_only``.
    Policy can only tighten from there: the WRITE branch leaves the
    existing FieldInfo untouched (so a field already ``read_only`` by
    static class stays read-only even if the policy says WRITE), READ
    forces read-only, HIDDEN drops the field. A policy therefore never
    loosens what the static config locked down — matching
    :meth:`FieldPermission.strictest`.
    """
    if ctx is None:
        return schema
    policy = getattr(admin_class, "policy", None)
    if policy is None:
        return schema
    from dataclasses import replace

    from asterion.admin.policy import FieldPermission

    new_fields: list[FieldInfo] = []
    for fi in schema.fields:
        perm = await policy.field_permission(fi.name, obj, ctx)
        if perm is FieldPermission.HIDDEN:
            continue  # drop entirely — clean_write_payload treats absence as "unknown field"
        if perm is FieldPermission.READ:
            new_fields.append(replace(fi, read_only=True))
        else:
            new_fields.append(fi)
    return AdminModelSchema(model_name=schema.model_name, fields=new_fields)


async def get_record_or_404(
    session: AsyncSession,
    admin_class: ModelAdmin,
    record_id: str,
) -> Any:
    model = admin_class.model
    pk_column = primary_key_column(model)
    pk_value = coerce_primary_key_value(model, record_id)

    result = await session.execute(select(model).where(pk_column == pk_value))
    record = result.scalar_one_or_none()

    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Record not found.",
        )

    return record


def _policy(admin_class: ModelAdmin):
    """Return the admin's :class:`AdminPolicy` instance or ``None``.

    Centralized accessor so the service code doesn't repeat the
    ``policy = getattr(admin_class, "policy", None)`` dance and so
    subclasses that explicitly set ``policy = None`` keep working.
    """
    return getattr(admin_class, "policy", None)


async def _augment_with_inlines(
    payload: dict[str, Any],
    session: AsyncSession,
    record: Any,
    admin_class: ModelAdmin,
) -> dict[str, Any]:
    """Attach an ``inlines`` block when the admin has any declared.

    The block always carries every inline (possibly empty list) so
    clients don't need to existence-check per table. Empty when the
    admin has no inlines at all — keeps the wire surface unchanged
    for the typical case."""
    from asterion.admin.inline import fetch_inline_children

    if not getattr(admin_class, "inlines", []):
        return payload
    payload["inlines"] = await fetch_inline_children(session, record, admin_class)
    return payload


def _deny(resource: str, action: str) -> HTTPException:
    """Standard 403 for a policy denial. Keep the message structurally
    identical to the permission-key denial so clients can't tell the
    two checks apart from the response alone."""
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Operation not permitted: {action} on {resource}",
    )


async def list_records(
    session: AsyncSession,
    admin_class: ModelAdmin,
    *,
    limit: int = 100,
    offset: int = 0,
    search: str | None = None,
    filters: dict[str, Any] | None = None,
    ordering: str | None = None,
    date_hierarchy: str | None = None,
    ctx: AdminContext | None = None,
) -> dict[str, Any]:
    model = admin_class.model

    if ctx is not None:
        policy = _policy(admin_class)
        if policy is not None and not await policy.can_view_model(ctx):
            raise _deny(admin_class.model_name, "list")

    normalized_limit, normalized_offset = normalize_limit_offset(
        limit=limit,
        offset=offset,
    )

    base_stmt = select(model)
    base_stmt = apply_filters(base_stmt, admin_class, filters or {})
    base_stmt = apply_date_hierarchy(base_stmt, admin_class, date_hierarchy)
    base_stmt = apply_search(base_stmt, admin_class, search)

    total = (await session.execute(count_statement_for(base_stmt))).scalar_one()

    list_stmt = apply_ordering(base_stmt, admin_class, ordering)
    list_stmt = list_stmt.limit(normalized_limit).offset(normalized_offset)

    result = await session.execute(list_stmt)
    records = result.scalars().all()

    if ctx is not None:
        items = await serialize_records_with_policy(list(records), admin_class, ctx)
    else:
        items = serialize_records(records, admin_class)

    return PageResult(
        items=items,
        total=total,
        limit=normalized_limit,
        offset=normalized_offset,
    ).to_dict()


async def read_record(
    session: AsyncSession,
    admin_class: ModelAdmin,
    record_id: str,
    *,
    ctx: AdminContext | None = None,
) -> dict[str, Any]:
    record = await get_record_or_404(session, admin_class, record_id)

    if ctx is not None:
        policy = _policy(admin_class)
        if policy is not None and not await policy.can_view_object(record, ctx):
            raise _deny(admin_class.model_name, "read")
        payload = await serialize_record_with_policy(record, admin_class, ctx)
    else:
        payload = serialize_record(record, admin_class)

    return await _augment_with_inlines(payload, session, record, admin_class)


async def create_record(
    session: AsyncSession,
    admin_class: ModelAdmin,
    payload: Mapping[str, Any],
    *,
    ctx: AdminContext | None = None,
) -> dict[str, Any]:
    """Insert one record.

    Hook order when ``ctx`` is supplied (B2):
    ``before_validate`` → schema clean → ``validate_create`` →
    ``before_create`` → DB insert → ``after_create``. Hooks run on the
    ModelAdmin instance; the registered instance is the one passed in
    here (the router resolves it via ``runtime.registry.get(name)``).

    ``ctx=None`` skips every hook — keeps non-HTTP callers (tests,
    background jobs that haven't yet built a context) working unchanged.
    """
    from asterion.admin.inline import process_inline_writes, split_parent_payload

    model = admin_class.model
    schema = build_model_schema(admin_class)

    if ctx is not None:
        policy = _policy(admin_class)
        if policy is not None and not await policy.can_create(ctx):
            raise _deny(admin_class.model_name, "create")
        # Field-level policy decisions on a brand-new record have no
        # ``obj`` yet — pass None. The policy decides solely on caller +
        # field name.
        schema = await _apply_field_policy_to_schema(schema, admin_class, ctx, obj=None)

    # Strip ``inlines`` from the payload before any schema validation —
    # inlines are not columns on the parent model.
    parent_payload, inline_payload = split_parent_payload(payload)

    payload_in: dict[str, Any] = dict(parent_payload)
    if ctx is not None:
        payload_in = await admin_class.before_validate(payload_in, ctx)

    cleaned = clean_write_payload(
        payload_in,
        schema,
        partial=False,
    )

    if ctx is not None:
        await admin_class.validate_create(cleaned, ctx)
        cleaned = await admin_class.before_create(cleaned, ctx)

    record = model(**cleaned)

    session.add(record)
    await session.flush()
    await session.refresh(record)

    # Inline children write into the same session/transaction — any
    # failure here rolls the parent insert back, satisfying the
    # all-or-nothing contract.
    await process_inline_writes(session, record, admin_class, inline_payload, ctx=ctx)

    if ctx is not None:
        await admin_class.after_create(record, ctx)
        payload = await serialize_record_with_policy(record, admin_class, ctx)
    else:
        payload = serialize_record(record, admin_class, schema=schema)

    return await _augment_with_inlines(payload, session, record, admin_class)


async def update_record(
    session: AsyncSession,
    admin_class: ModelAdmin,
    record_id: str,
    payload: Mapping[str, Any],
    *,
    ctx: AdminContext | None = None,
) -> dict[str, Any]:
    """Patch one record.

    Hook order with ``ctx`` (B4 moves the fetch up so the field-level
    policy can inspect the row):
    fetch obj → ``can_update_object`` → schema + field policy →
    ``before_validate`` → schema clean → ``validate_update`` →
    ``before_update`` → apply changes → flush+refresh →
    ``after_update(obj, changes, ctx)``.

    ``changes`` is the post-``before_update`` payload that was actually
    written, not the raw input — matches what audit consumers want.
    """
    from asterion.admin.inline import process_inline_writes, split_parent_payload

    schema = build_model_schema(admin_class)

    # Strip ``inlines`` first; the parent-side payload is what goes
    # through clean_write_payload + hooks, the inline block is handed
    # to the dedicated writer after the parent is updated.
    parent_payload, inline_payload = split_parent_payload(payload)

    # When no ctx is supplied (legacy callers), fall back to the
    # pre-B4 order: schema-clean first, fetch later. No policy hooks
    # run anyway, so the simpler path is sufficient. Inline writes
    # still happen so non-HTTP callers (tests, scripts) can use the
    # same wire format.
    if ctx is None:
        cleaned = clean_write_payload(parent_payload, schema, partial=True)
        record = await get_record_or_404(session, admin_class, record_id)
        for field_name, value in cleaned.items():
            setattr(record, field_name, value)
        await session.flush()
        await session.refresh(record)
        await process_inline_writes(session, record, admin_class, inline_payload, ctx=ctx)
        payload = serialize_record(record, admin_class, schema=schema)
        return await _augment_with_inlines(payload, session, record, admin_class)

    # ctx-aware path: fetch first so field policy can use the row.
    record = await get_record_or_404(session, admin_class, record_id)

    policy = _policy(admin_class)
    if policy is not None and not await policy.can_update_object(record, ctx):
        raise _deny(admin_class.model_name, "update")

    schema = await _apply_field_policy_to_schema(schema, admin_class, ctx, obj=record)

    payload_in: dict[str, Any] = dict(parent_payload)
    payload_in = await admin_class.before_validate(payload_in, ctx)

    cleaned = clean_write_payload(
        payload_in,
        schema,
        partial=True,
    )

    await admin_class.validate_update(record, cleaned, ctx)
    cleaned = await admin_class.before_update(record, cleaned, ctx)

    for field_name, value in cleaned.items():
        setattr(record, field_name, value)

    await session.flush()
    await session.refresh(record)

    await process_inline_writes(session, record, admin_class, inline_payload, ctx=ctx)

    await admin_class.after_update(record, cleaned, ctx)

    payload = await serialize_record_with_policy(record, admin_class, ctx)
    return await _augment_with_inlines(payload, session, record, admin_class)


async def delete_record(
    session: AsyncSession,
    admin_class: ModelAdmin,
    record_id: str,
    *,
    ctx: AdminContext | None = None,
) -> dict[str, Any]:
    """Delete one record.

    Hook order with ``ctx``: fetch obj → ``before_delete`` → ``is_system``
    guard → ``session.delete`` → ``after_delete``. The ``is_system``
    guard stays inside the framework because it's a hard invariant
    (built-in tables must not be deletable by CRUD), not a per-app
    policy decision.
    """
    record = await get_record_or_404(session, admin_class, record_id)

    if ctx is not None:
        policy = _policy(admin_class)
        if policy is not None and not await policy.can_delete_object(record, ctx):
            raise _deny(admin_class.model_name, "delete")
        await admin_class.before_delete(record, ctx)

    if getattr(record, "is_system", False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="System records cannot be deleted.",
        )

    await session.delete(record)
    await session.flush()

    if ctx is not None:
        await admin_class.after_delete(record, ctx)

    return {"deleted": True}
