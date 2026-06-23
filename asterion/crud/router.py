from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from asterion.admin.context import AdminContext, require_admin_context
from asterion.audit import (
    CRUD_CREATE,
    CRUD_DELETE,
    CRUD_UPDATE,
    record_audit_in_session,
    request_audit_kwargs,
)
from asterion.authz.permissions import require_resource_access
from asterion.crud.services import (
    create_record,
    delete_record,
    list_records,
    read_record,
    update_record,
)
from asterion.db.dependencies import get_async_session
from asterion.registry import ModelAdmin
from asterion.security.validation import (
    InvalidResourceNameError,
    validate_resource_name,
)

logger = logging.getLogger(__name__)


def _get_admin_class(request: Request, resource: str) -> ModelAdmin:
    try:
        resource = validate_resource_name(resource)
    except InvalidResourceNameError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Resource '{resource}' is not registered.",
        ) from None
    admin_class = request.app.state.asterion.registry.get(resource)
    if admin_class is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Resource '{resource}' is not registered.",
        )
    return admin_class


def _require_resource_permission(
    ctx: AdminContext,
    resource: str,
    action: str,
) -> None:
    """Authorize ``action`` on ``resource`` — see
    :func:`asterion.authz.permissions.require_resource_access`."""
    require_resource_access(ctx, resource, action)


async def _audit_crud(
    session: AsyncSession,
    request: Request,
    *,
    action: str,
    status_code: int,
    ctx: AdminContext,
    resource: str,
    record_id: str | int | None = None,
    changes: dict[str, Any] | None = None,
) -> None:
    """Defense in depth: wrap the in-session audit helper so any failure
    short of an OS-level error is logged and not surfaced as a 500.

    ``ctx.principal`` is duck-typing compatible with the audit helper's
    ``actor: User | None`` — both expose ``.id`` and ``.email``.
    """
    try:
        kwargs = request_audit_kwargs(request, status_code=status_code)
        if ctx.tenant is not None:
            # Inside a tenant the session is tenant-scoped, so the audit row
            # goes to that tenant's own audit_logs table (physical isolation),
            # not the public one. Global/cross-tenant events stay on the
            # public table with tenant_id as a discriminator.
            kwargs["tenant_id"] = ctx.tenant.id
            kwargs["tenant_scoped"] = True
        await record_audit_in_session(
            session,
            action=action,
            actor=ctx.principal,
            resource=resource,
            record_id=record_id,
            changes=changes,
            **kwargs,
        )
    except Exception:
        logger.warning(
            "crud audit hook failed for action=%s resource=%s",
            action,
            resource,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Handler implementations — the resource is a plain argument (bound per route
# by ``build_crud_router``), NOT a path parameter. This keeps the dispatch
# logic in one place while the path registration becomes explicit.
# ---------------------------------------------------------------------------


async def _list_impl(
    resource: str,
    request: Request,
    *,
    limit: int,
    offset: int,
    search: str | None,
    ordering: str | None,
    dh: str | None,
    session: AsyncSession,
    ctx: AdminContext,
) -> dict[str, Any]:
    from asterion.crud.query import parse_filter_query

    admin_class = _get_admin_class(request, resource)
    _require_resource_permission(ctx, admin_class.model_name, "list")
    filters = parse_filter_query(request.query_params, admin_class)
    return await list_records(
        session,
        admin_class,
        limit=limit,
        offset=offset,
        search=search,
        filters=filters,
        ordering=ordering,
        date_hierarchy=dh,
        ctx=ctx,
    )


async def _create_impl(
    resource: str,
    request: Request,
    *,
    session: AsyncSession,
    ctx: AdminContext,
) -> dict[str, Any]:
    admin_class = _get_admin_class(request, resource)
    _require_resource_permission(ctx, admin_class.model_name, "create")
    payload = await request.json()
    result = await create_record(session, admin_class, payload, ctx=ctx)
    await _audit_crud(
        session,
        request,
        action=CRUD_CREATE,
        status_code=201,
        ctx=ctx,
        resource=admin_class.model_name,
        record_id=result.get("id"),
        changes=payload,
    )
    return result


async def _read_impl(
    resource: str,
    record_id: str,
    request: Request,
    *,
    session: AsyncSession,
    ctx: AdminContext,
) -> dict[str, Any]:
    admin_class = _get_admin_class(request, resource)
    _require_resource_permission(ctx, admin_class.model_name, "read")
    return await read_record(session, admin_class, record_id, ctx=ctx)


async def _update_impl(
    resource: str,
    record_id: str,
    request: Request,
    *,
    session: AsyncSession,
    ctx: AdminContext,
) -> dict[str, Any]:
    admin_class = _get_admin_class(request, resource)
    _require_resource_permission(ctx, admin_class.model_name, "update")
    payload = await request.json()
    result = await update_record(session, admin_class, record_id, payload, ctx=ctx)
    await _audit_crud(
        session,
        request,
        action=CRUD_UPDATE,
        status_code=200,
        ctx=ctx,
        resource=admin_class.model_name,
        record_id=record_id,
        changes=payload,
    )
    return result


async def _fk_options_impl(
    resource: str,
    field: str,
    request: Request,
    *,
    limit: int,
    q: str | None,
    session: AsyncSession,
    ctx: AdminContext,
) -> dict[str, Any]:
    """Enumerate ``{value, label}`` options for a foreign-key column.

    Powers the form FK dropdown: given the resource carrying the FK and the
    column name, resolve the target table, look up its registered admin, and
    return its rows as id → human-readable label (the target admin's
    :attr:`~asterion.registry.ModelAdmin.label_field`).

    Authorization: the caller must be able to ``read`` the resource that owns
    the FK *and* ``list`` the target resource (the options come from it).

    Returns an empty option list (never a 500) when the target isn't a
    registered admin, or when it lives in a different schema scope than the
    owning resource — the cross-schema case (e.g. a tenant row referencing a
    public table) is resolved by a dedicated path, not the generic query here.
    """
    from sqlalchemy import String, cast, select

    from asterion.contract.service import resolve_model_scope
    from asterion.crud.query import get_model_column, primary_key_column

    admin_class = _get_admin_class(request, resource)
    _require_resource_permission(ctx, admin_class.model_name, "read")

    try:
        column = get_model_column(admin_class.model, field)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Field '{field}' is not a column of '{resource}'.",
        ) from None
    fks = list(column.foreign_keys)
    if not fks:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Field '{field}' is not a foreign key.",
        )

    target_table = fks[0].column.table.name
    target_admin = request.app.state.asterion.registry.get(target_table)
    if target_admin is None:
        # Target table isn't managed by the admin — nothing to enumerate.
        return {"options": [], "truncated": False, "registered": False}

    # Cross-schema FK (tenant ↔ public): the generic query below would run
    # against the wrong search_path. Defer to the dedicated path; until then
    # the form falls back to a raw input.
    if resolve_model_scope(target_admin) != resolve_model_scope(admin_class):
        return {"options": [], "truncated": False, "registered": True, "cross_scope": True}

    _require_resource_permission(ctx, target_admin.model_name, "list")

    target_model = target_admin.model
    pk_col = primary_key_column(target_model)
    label_col = getattr(target_model, target_admin.label_field)

    stmt = select(pk_col, label_col)
    if q and q.strip():
        stmt = stmt.where(cast(label_col, String).ilike(f"%{q.strip()}%"))
    # Fetch one extra row to detect truncation without a separate count.
    stmt = stmt.order_by(label_col.asc()).limit(limit + 1)

    rows = (await session.execute(stmt)).all()
    truncated = len(rows) > limit
    options = [
        {"value": str(pk), "label": str(label) if label is not None else str(pk)}
        for pk, label in rows[:limit]
    ]
    return {"options": options, "truncated": truncated, "registered": True}


async def _delete_impl(
    resource: str,
    record_id: str,
    request: Request,
    *,
    session: AsyncSession,
    ctx: AdminContext,
) -> dict[str, Any]:
    admin_class = _get_admin_class(request, resource)
    _require_resource_permission(ctx, admin_class.model_name, "delete")
    result = await delete_record(session, admin_class, record_id, ctx=ctx)
    await _audit_crud(
        session,
        request,
        action=CRUD_DELETE,
        status_code=200,
        ctx=ctx,
        resource=admin_class.model_name,
        record_id=record_id,
    )
    return result


def _register_resource(router: APIRouter, resource: str) -> None:
    """Register the five CRUD routes for one resource under explicit paths
    (``/employees`` instead of ``/{resource}``). Each handler closes over
    ``resource`` so the dispatch impls above are reused verbatim."""

    @router.get(f"/{resource}", name=f"crud_list_{resource}")
    async def crud_list(
        request: Request,
        limit: int = 100,
        offset: int = 0,
        search: str | None = None,
        ordering: str | None = None,
        dh: str | None = None,
        session: AsyncSession = Depends(get_async_session),
        ctx: AdminContext = Depends(require_admin_context),
    ) -> dict[str, Any]:
        return await _list_impl(
            resource,
            request,
            limit=limit,
            offset=offset,
            search=search,
            ordering=ordering,
            dh=dh,
            session=session,
            ctx=ctx,
        )

    @router.post(
        f"/{resource}", status_code=status.HTTP_201_CREATED, name=f"crud_create_{resource}"
    )
    async def crud_create(
        request: Request,
        session: AsyncSession = Depends(get_async_session),
        ctx: AdminContext = Depends(require_admin_context),
    ) -> dict[str, Any]:
        return await _create_impl(resource, request, session=session, ctx=ctx)

    # FK dropdown options. A 3-segment path (`/{resource}/_options/{field}`)
    # so it never collides with the 2-segment `/{resource}/{record_id}` routes.
    @router.get(f"/{resource}/_options/{{field}}", name=f"crud_options_{resource}")
    async def crud_options(
        field: str,
        request: Request,
        limit: int = 100,
        q: str | None = None,
        session: AsyncSession = Depends(get_async_session),
        ctx: AdminContext = Depends(require_admin_context),
    ) -> dict[str, Any]:
        return await _fk_options_impl(
            resource, field, request, limit=limit, q=q, session=session, ctx=ctx
        )

    @router.get(f"/{resource}/{{record_id}}", name=f"crud_read_{resource}")
    async def crud_read(
        record_id: str,
        request: Request,
        session: AsyncSession = Depends(get_async_session),
        ctx: AdminContext = Depends(require_admin_context),
    ) -> dict[str, Any]:
        return await _read_impl(resource, record_id, request, session=session, ctx=ctx)

    @router.patch(f"/{resource}/{{record_id}}", name=f"crud_update_{resource}")
    async def crud_update(
        record_id: str,
        request: Request,
        session: AsyncSession = Depends(get_async_session),
        ctx: AdminContext = Depends(require_admin_context),
    ) -> dict[str, Any]:
        return await _update_impl(resource, record_id, request, session=session, ctx=ctx)

    @router.delete(f"/{resource}/{{record_id}}", name=f"crud_delete_{resource}")
    async def crud_delete(
        record_id: str,
        request: Request,
        session: AsyncSession = Depends(get_async_session),
        ctx: AdminContext = Depends(require_admin_context),
    ) -> dict[str, Any]:
        return await _delete_impl(resource, record_id, request, session=session, ctx=ctx)


def build_crud_router(resources: Iterable[str]) -> APIRouter:
    """Build a CRUD router with EXPLICIT per-resource paths.

    Instead of one greedy ``/{resource}`` catch-all, this registers
    ``/employees``, ``/projects``, … for each frozen resource. A path under the
    admin prefix that is NOT a registered resource then matches no CRUD route,
    leaving it free for an embedding app to claim via ``app.include_router``
    after ``create_admin`` (no AdminExtension / route-ordering tricks needed).
    """
    router = APIRouter()
    for resource in resources:
        _register_resource(router, resource)
    return router
