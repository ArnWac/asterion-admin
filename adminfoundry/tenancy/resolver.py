"""Tenant resolution: slug extraction, caching, DB lookup, impersonation."""
from __future__ import annotations

import json
import time
import uuid

from sqlalchemy import select
from starlette.requests import Request

from adminfoundry.database import AsyncSessionLocal
from adminfoundry.models.tenant import Tenant
from adminfoundry.schemas.tenant import RESERVED_SLUGS
from adminfoundry.settings import settings
from adminfoundry.tenancy.context import TenantContext

_TENANT_TTL = 30  # seconds
_REDIS_PREFIX = "tenant:"

_tenant_cache: dict[str, tuple] = {}


def clear_tenant_cache() -> None:
    _tenant_cache.clear()


def _mem_get(slug: str) -> tuple[bool, TenantContext | None]:
    entry = _tenant_cache.get(slug)
    if entry and time.monotonic() < entry[1]:
        return True, entry[0]
    return False, None


def _mem_set(slug: str, ctx: TenantContext | None) -> None:
    _tenant_cache[slug] = (ctx, time.monotonic() + _TENANT_TTL)


def _serialize(ctx: TenantContext) -> str:
    return json.dumps(ctx.to_dict())


def _deserialize(raw: str) -> TenantContext | None:
    data = json.loads(raw)
    if data is None:
        return None
    return TenantContext.from_dict(data)


async def _redis_get(client, slug: str) -> tuple[bool, TenantContext | None]:
    raw = await client.get(f"{_REDIS_PREFIX}{slug}")
    if raw is None:
        return False, None
    return True, _deserialize(raw)


async def _redis_set(client, slug: str, ctx: TenantContext | None) -> None:
    value = _serialize(ctx) if ctx is not None else json.dumps(None)
    await client.setex(f"{_REDIS_PREFIX}{slug}", _TENANT_TTL, value)


def _extract_slug(request: Request) -> str | None:
    if settings.TENANT_RESOLUTION_STRATEGY == "subdomain":
        host = request.headers.get("host", "").split(":")[0]
        parts = host.split(".")
        if len(parts) >= 2:
            candidate = parts[0]
            if candidate not in RESERVED_SLUGS:
                return candidate
        return None
    return request.headers.get("X-Tenant-Slug")


async def resolve_tenant(request: Request) -> TenantContext | None:
    """Resolve and return the TenantContext for the request, or None."""
    slug = _extract_slug(request)
    if not slug:
        return None

    from adminfoundry.redis_client import get_redis
    client = get_redis()

    if client:
        hit, ctx = await _redis_get(client, slug)
    else:
        hit, ctx = _mem_get(slug)

    if not hit:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Tenant).where(Tenant.slug == slug))
            tenant = result.scalar_one_or_none()
        ctx = TenantContext.from_orm(tenant) if tenant is not None else None
        if client:
            await _redis_set(client, slug, ctx)
        else:
            _mem_set(slug, ctx)

    return ctx


async def resolve_impersonation_tenant(
    payload: dict, current_tenant: TenantContext | None, db
) -> TenantContext | None:
    """Return TenantContext for a same-origin impersonation token.

    Returns current_tenant unchanged when it is already set, or when the
    token carries no tenant_id.
    """
    if current_tenant is not None:
        return current_tenant
    if not (payload.get("impersonated_by") and payload.get("tenant_id")):
        return None
    tenant = (
        await db.execute(
            select(Tenant).where(Tenant.id == uuid.UUID(payload["tenant_id"]))
        )
    ).scalar_one_or_none()
    return TenantContext.from_orm(tenant) if tenant is not None else None
