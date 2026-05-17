"""Async helpers to load RolePermission records from DB for policy evaluation."""
from __future__ import annotations
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from adminfoundry.models.role_permission import RolePermission


def _role_ids_from(user, membership) -> list:
    # In tenant context use membership roles only — prevents cross-tenant role leakage.
    if membership is not None:
        return [r.id for r in (getattr(membership, "roles", None) or [])]
    return [r.id for r in (getattr(user, "roles", None) or [])]


async def fetch_model_caps(
    user, model_name: str, db: AsyncSession, membership=None
) -> dict | None:
    """Return merged caps dict for user+model, or None if no DB records exist."""
    role_ids = _role_ids_from(user, membership)
    if not role_ids:
        return None
    rows = (
        await db.execute(
            select(RolePermission).where(
                RolePermission.role_id.in_(role_ids),
                RolePermission.model_name == model_name,
            )
        )
    ).scalars().all()
    if not rows:
        return None
    return {
        "can_list": any(r.can_list for r in rows),
        "can_create": any(r.can_create for r in rows),
        "can_read": any(r.can_list for r in rows),
        "can_update": any(r.can_update for r in rows),
        "can_delete": any(r.can_delete for r in rows),
    }


async def fetch_all_model_caps(user, db: AsyncSession, membership=None) -> dict[str, dict]:
    """Return {model_name: caps_dict} for all RolePermissions of the user's roles."""
    role_ids = _role_ids_from(user, membership)
    if not role_ids:
        return {}
    rows = (
        await db.execute(
            select(RolePermission).where(RolePermission.role_id.in_(role_ids))
        )
    ).scalars().all()

    merged: dict[str, dict] = {}
    for row in rows:
        mn = row.model_name
        if mn not in merged:
            merged[mn] = dict(can_list=False, can_create=False, can_read=False, can_update=False, can_delete=False)
        merged[mn]["can_list"] = merged[mn]["can_list"] or row.can_list
        merged[mn]["can_create"] = merged[mn]["can_create"] or row.can_create
        merged[mn]["can_read"] = merged[mn]["can_read"] or row.can_list
        merged[mn]["can_update"] = merged[mn]["can_update"] or row.can_update
        merged[mn]["can_delete"] = merged[mn]["can_delete"] or row.can_delete
    return merged
