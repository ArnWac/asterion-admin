"""Tenant resolution: slug extraction, in-memory caching, DB lookup."""

from __future__ import annotations

import time

from sqlalchemy import select
from starlette.requests import Request

from asterion.models.tenant import Tenant
from asterion.tenancy.context import TenantContext

_DEFAULT_TENANT_TTL = 30  # seconds; overridable via config.tenant_cache_ttl_seconds
_tenant_cache: dict[str, tuple] = {}


def clear_tenant_cache() -> None:
    """Drop the whole per-process tenant cache."""
    _tenant_cache.clear()


def invalidate_tenant(slug: str) -> None:
    """Evict one slug from the per-process cache (Review R9).

    Call this from a *same-process* mutation path (e.g. a Tenant ModelAdmin
    write hook) right after changing a tenant's ``is_active`` / ``allowed_cidrs``
    so the change takes effect immediately instead of after the TTL. Note this
    only clears the calling process's cache — out-of-process changes (the CLI,
    another worker) still propagate within ``tenant_cache_ttl_seconds``.
    """
    _tenant_cache.pop(slug, None)


def _mem_get(slug: str) -> tuple[bool, TenantContext | None]:
    entry = _tenant_cache.get(slug)
    if entry and time.monotonic() < entry[1]:
        return True, entry[0]
    return False, None


def _mem_set(slug: str, ctx: TenantContext | None, ttl: float = _DEFAULT_TENANT_TTL) -> None:
    # ttl <= 0 means "do not cache" — the entry expires immediately so the
    # next lookup misses and re-reads from the DB.
    if ttl <= 0:
        _tenant_cache.pop(slug, None)
        return
    _tenant_cache[slug] = (ctx, time.monotonic() + ttl)


def _get_resolution_strategy(request: Request) -> tuple[str, str]:
    """Return (strategy, header_name) from runtime config or defaults."""
    runtime = getattr(getattr(request.app, "state", None), "asterion", None)
    if runtime is not None:
        return (
            runtime.config.tenant_resolution or "header",
            runtime.config.tenant_header_name or "X-Tenant-Slug",
        )
    return "header", "X-Tenant-Slug"


def _normalize_slug(value: str | None) -> str | None:
    """Canonicalize an inbound slug (Review R12): strip + lowercase so a
    client sending ``"Acme"`` / ``" acme "`` resolves the same tenant that was
    stored as ``"acme"``. Empty after trimming → ``None``."""
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None


def _extract_slug(request: Request) -> str | None:
    strategy, header_name = _get_resolution_strategy(request)
    if strategy == "subdomain":
        host = request.headers.get("host", "").split(":")[0]
        parts = host.split(".")
        if len(parts) >= 2:
            return _normalize_slug(parts[0])
        return None
    return _normalize_slug(request.headers.get(header_name))


async def resolve_tenant(request: Request) -> TenantContext | None:
    """Resolve and return the TenantContext for the request, or None."""
    slug = _extract_slug(request)
    if not slug:
        return None

    hit, ctx = _mem_get(slug)
    if not hit:
        runtime = request.app.state.asterion
        async with runtime.db.session() as session:
            result = await session.execute(select(Tenant).where(Tenant.slug == slug))
            tenant = result.scalar_one_or_none()
        ctx = TenantContext.from_orm(tenant) if tenant is not None else None
        ttl = getattr(runtime.config, "tenant_cache_ttl_seconds", _DEFAULT_TENANT_TTL)
        _mem_set(slug, ctx, ttl)

    return ctx
