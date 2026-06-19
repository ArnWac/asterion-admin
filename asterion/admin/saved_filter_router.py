"""HTTP surface for saved list-view filters (D2).

Per-user storage of named list-view configurations (filters + search +
ordering). The wire format is opaque from the framework's perspective:
the UI POSTs whatever dict it wants under ``payload``, GETs the list
back, and replays the saved entries against the list endpoint.

Endpoints
---------

``POST   /api/v1/admin/_saved_filters``
    Create or replace a saved filter. Body::

        {"resource": "posts", "name": "drafts", "payload": {...}}

    The (user, tenant, resource, name) tuple is unique — a second
    POST with the same name overwrites the prior entry.

``GET    /api/v1/admin/_saved_filters?resource=posts``
    List the calling user's saved filters for one resource. Returns
    ``[{id, name, payload, created_at}, ...]``.

``DELETE /api/v1/admin/_saved_filters/{id}``
    Delete a saved filter owned by the calling user. 404 if the
    target doesn't exist or belongs to someone else (no cross-user
    information leak).

Scope
-----

* ``user_id`` is ``ctx.principal.id`` — saved filters are private.
* ``tenant_id`` is ``ctx.tenant.id`` (or None for root scope) — saved
  filters never leak across tenants.
* No permission key gate; if the caller can hit the admin at all,
  they may manage their own saved filters.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from asterion.admin.context import AdminContext, require_admin_context
from asterion.db.dependencies import get_async_session
from asterion.models.saved_filter import SavedFilter
from asterion.security.validation import (
    InvalidResourceNameError,
    validate_resource_name,
)

router = APIRouter()

MAX_NAME_LEN = 200


def _user_id(ctx: AdminContext) -> str:
    """Pull a stable string identifier for the calling principal."""
    if ctx.principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    return str(ctx.principal.id)


def _tenant_id(ctx: AdminContext) -> str | None:
    return str(ctx.tenant.id) if ctx.tenant is not None else None


def _serialize(row: SavedFilter) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "resource": row.resource,
        "name": row.name,
        "payload": row.payload or {},
        "created_at": row.created_at.isoformat() if row.created_at is not None else None,
    }


@router.post("/_saved_filters", status_code=status.HTTP_201_CREATED)
async def create_saved_filter(
    payload: dict[str, Any] = Body(...),
    session: AsyncSession = Depends(get_async_session),
    ctx: AdminContext = Depends(require_admin_context),
) -> dict[str, Any]:
    try:
        resource = validate_resource_name(payload.get("resource", ""))
    except InvalidResourceNameError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid 'resource' value.",
        ) from None

    name = str(payload.get("name", "")).strip()
    if not name or len(name) > MAX_NAME_LEN:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"'name' is required and must be ≤ {MAX_NAME_LEN} characters.",
        )

    body = payload.get("payload", {})
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="'payload' must be an object.",
        )

    user_id = _user_id(ctx)
    tenant_id = _tenant_id(ctx)

    # Upsert semantics: replace any existing row for the same
    # (user, tenant, resource, name) tuple.
    existing = await session.execute(
        select(SavedFilter).where(
            SavedFilter.user_id == user_id,
            SavedFilter.tenant_id == tenant_id,
            SavedFilter.resource == resource,
            SavedFilter.name == name,
        )
    )
    row = existing.scalar_one_or_none()
    if row is not None:
        row.payload = body
    else:
        row = SavedFilter(
            user_id=user_id,
            tenant_id=tenant_id,
            resource=resource,
            name=name,
            payload=body,
        )
        session.add(row)

    await session.flush()
    return _serialize(row)


@router.get("/_saved_filters")
async def list_saved_filters(
    resource: str = Query(...),
    session: AsyncSession = Depends(get_async_session),
    ctx: AdminContext = Depends(require_admin_context),
) -> list[dict[str, Any]]:
    try:
        resource = validate_resource_name(resource)
    except InvalidResourceNameError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid 'resource' value.",
        ) from None

    result = await session.execute(
        select(SavedFilter)
        .where(
            SavedFilter.user_id == _user_id(ctx),
            SavedFilter.tenant_id == _tenant_id(ctx),
            SavedFilter.resource == resource,
        )
        .order_by(SavedFilter.created_at.desc())
    )
    rows = result.scalars().all()
    return [_serialize(r) for r in rows]


@router.delete("/_saved_filters/{filter_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saved_filter(
    filter_id: str,
    session: AsyncSession = Depends(get_async_session),
    ctx: AdminContext = Depends(require_admin_context),
) -> None:
    import uuid as _uuid

    try:
        as_uuid = _uuid.UUID(filter_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Saved filter not found.",
        ) from None

    # Scope by owner + tenant so deleting somebody else's entry is a
    # 404, not a 403 — keeps the existence of other users' filters
    # invisible.
    result = await session.execute(
        delete(SavedFilter).where(
            SavedFilter.id == as_uuid,
            SavedFilter.user_id == _user_id(ctx),
            SavedFilter.tenant_id == _tenant_id(ctx),
        )
    )
    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Saved filter not found.",
        )
    return None
