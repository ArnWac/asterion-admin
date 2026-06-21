"""Admin actions router.

Two endpoint shapes (D3):

* ``POST /api/v1/admin/{resource}/_actions/{action}`` — bulk action
  with ``{"ids": [...], "data": {...}}`` body. Empty ``ids`` is
  allowed for actions that don't depend on a row selection.
* ``POST /api/v1/admin/{resource}/{record_id}/_actions/{action}`` —
  row action with ``{"data": {...}}`` body. The record id is part
  of the URL, so the action sees ``len(objects) == 1``.

Both endpoints share the same permission gate, dispatch, audit, and
schema validation so the action implementation never has to branch
on which shape was used.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asterion.actions import AdminAction, uses_typed_run
from asterion.admin.context import AdminContext, require_admin_context
from asterion.audit import (
    ADMIN_ACTION,
    record_audit_in_session,
    request_audit_kwargs,
)
from asterion.authz.permissions import require_resource_access
from asterion.crud.query import coerce_primary_key_value, primary_key_column
from asterion.db.dependencies import get_async_session
from asterion.registry import ModelAdmin
from asterion.security.validation import (
    InvalidActionNameError,
    InvalidResourceNameError,
    validate_action_name,
    validate_resource_name,
)

logger = logging.getLogger(__name__)


class ActionRequest(BaseModel):
    ids: list[Any] = Field(default_factory=list)
    #: Optional typed payload for actions that declare an
    #: ``input_schema``. The router validates ``data`` against the
    #: action's schema before dispatch — invalid input → 422.
    data: dict[str, Any] | None = None


class RowActionRequest(BaseModel):
    """Body for the single-row endpoint — no ``ids`` (the row id is
    in the URL). Only ``data`` is meaningful and only when the
    action declares an ``input_schema``."""

    data: dict[str, Any] | None = None


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _resolve_admin(request: Request, resource: str) -> type[ModelAdmin]:
    try:
        resource = validate_resource_name(resource)
    except InvalidResourceNameError:
        raise _not_found(f"Resource '{resource}' is not registered.") from None
    admin = request.app.state.asterion.registry.get(resource)
    if admin is None:
        raise _not_found(f"Resource '{resource}' is not registered.")
    return admin


def _resolve_action(admin: ModelAdmin, action_name: str) -> AdminAction:
    try:
        action_name = validate_action_name(action_name)
    except InvalidActionNameError:
        raise _not_found(f"Action '{action_name}' is not declared.") from None
    for candidate in admin.actions:
        if getattr(candidate, "name", None) == action_name:
            return candidate
    raise _not_found(f"Action '{action_name}' is not declared.")


def _require_permission(
    ctx: AdminContext,
    resource: str,
    action: str,
) -> None:
    require_resource_access(ctx, resource, action)


async def _resolve_records(
    session: AsyncSession,
    admin: ModelAdmin,
    raw_ids: list[Any],
) -> list[Any]:
    if not raw_ids:
        return []
    model = admin.model
    pk_column = primary_key_column(model)
    coerced = [coerce_primary_key_value(model, str(raw)) for raw in raw_ids]
    result = await session.execute(select(model).where(pk_column.in_(coerced)))
    return list(result.scalars().all())


async def _dispatch_action(
    *,
    action_instance: AdminAction,
    records: list[Any],
    data_in: dict[str, Any] | None,
    session: AsyncSession,
    ctx: AdminContext,
) -> dict[str, Any]:
    """Shared dispatch path for bulk + row endpoints.

    Picks ``run`` over ``execute`` when the subclass overrides it,
    validates the typed input, and normalises the result into a
    ``dict``. Raises HTTPException on bad input / bad return shape."""
    if uses_typed_run(action_instance):
        data: Any = data_in or {}
        if action_instance.input_schema is not None:
            try:
                data = action_instance.input_schema.model_validate(data)
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail={"message": "Invalid action input.", "error": str(exc)},
                ) from exc
        result = await action_instance.run(records, data, ctx)
    else:
        result = await action_instance.execute(records, session, ctx.principal)

    if not isinstance(result, dict):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Action did not return a dict result.",
        )
    return result


async def _write_action_audit(
    *,
    session: AsyncSession,
    request: Request,
    admin: ModelAdmin,
    action_instance: AdminAction,
    ids: list[Any],
    affected: Any,
    ctx: AdminContext,
) -> None:
    try:
        await record_audit_in_session(
            session,
            action=ADMIN_ACTION,
            actor=ctx.principal,
            resource=admin.model_name,
            tenant_id=ctx.tenant.id if ctx.tenant is not None else None,
            changes={
                "action": action_instance.name,
                "ids": [str(i) for i in ids],
                "affected": affected,
            },
            **request_audit_kwargs(request, status_code=200),
        )
    except Exception:
        logger.warning(
            "admin action audit hook failed for resource=%s action=%s",
            admin.model_name,
            action_instance.name,
            exc_info=True,
        )


async def _run_action_impl(
    resource: str,
    action: str,
    payload: ActionRequest,
    request: Request,
    session: AsyncSession,
    ctx: AdminContext,
) -> dict[str, Any]:
    """Bulk action — operates on the records identified by ``payload.ids``."""
    admin = _resolve_admin(request, resource)
    action_instance = _resolve_action(admin, action)
    _require_permission(ctx, admin.model_name, action_instance.name)
    records = await _resolve_records(session, admin, payload.ids)

    result = await _dispatch_action(
        action_instance=action_instance,
        records=records,
        data_in=payload.data,
        session=session,
        ctx=ctx,
    )
    await _write_action_audit(
        session=session,
        request=request,
        admin=admin,
        action_instance=action_instance,
        ids=payload.ids,
        affected=result.get("affected"),
        ctx=ctx,
    )
    return result


async def _run_row_action_impl(
    resource: str,
    record_id: str,
    action: str,
    payload: RowActionRequest,
    request: Request,
    session: AsyncSession,
    ctx: AdminContext,
) -> dict[str, Any]:
    """Row action — operates on the single record identified by the URL.

    The action receives ``records = [<the one row>]`` so existing
    bulk-style implementations Just Work; new actions can branch on
    ``len(records) == 1`` if they want row-specific behaviour. The
    URL-level record id is treated as authoritative — payload ids
    are not accepted on this route.
    """
    admin = _resolve_admin(request, resource)
    action_instance = _resolve_action(admin, action)
    _require_permission(ctx, admin.model_name, action_instance.name)

    records = await _resolve_records(session, admin, [record_id])
    if not records:
        raise _not_found(f"Record {record_id!r} not found in {admin.model_name!r}.")

    result = await _dispatch_action(
        action_instance=action_instance,
        records=records,
        data_in=payload.data,
        session=session,
        ctx=ctx,
    )
    await _write_action_audit(
        session=session,
        request=request,
        admin=admin,
        action_instance=action_instance,
        ids=[record_id],
        affected=result.get("affected"),
        ctx=ctx,
    )
    return result


def _register_resource_actions(router: APIRouter, resource: str) -> None:
    """Register the bulk + row action routes for one resource under explicit
    paths (``/employees/_actions/{action}``), each closing over ``resource``."""

    @router.post(f"/{resource}/_actions/{{action}}", name=f"run_action_{resource}")
    async def run_action(
        action: str,
        payload: ActionRequest,
        request: Request,
        session: AsyncSession = Depends(get_async_session),
        ctx: AdminContext = Depends(require_admin_context),
    ) -> dict[str, Any]:
        return await _run_action_impl(resource, action, payload, request, session, ctx)

    @router.post(
        f"/{resource}/{{record_id}}/_actions/{{action}}", name=f"run_row_action_{resource}"
    )
    async def run_row_action(
        record_id: str,
        action: str,
        payload: RowActionRequest,
        request: Request,
        session: AsyncSession = Depends(get_async_session),
        ctx: AdminContext = Depends(require_admin_context),
    ) -> dict[str, Any]:
        return await _run_row_action_impl(
            resource, record_id, action, payload, request, session, ctx
        )


def build_actions_router(resources: Iterable[str]) -> APIRouter:
    """Build an actions router with EXPLICIT per-resource paths instead of a
    greedy ``/{resource}/_actions/{action}`` catch-all — see
    :func:`asterion.crud.router.build_crud_router`."""
    router = APIRouter()
    for resource in resources:
        _register_resource_actions(router, resource)
    return router
