import json
import time
import uuid
from types import SimpleNamespace
from fastapi.responses import JSONResponse
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from adminfoundry.database import AsyncSessionLocal
from adminfoundry.models.tenant import Tenant
from adminfoundry.schemas.tenant import RESERVED_SLUGS
from adminfoundry.settings import settings

_TENANT_TTL = 30  # seconds
_REDIS_PREFIX = "tenant:"

# Fallback in-memory cache: slug → (tenant | None, monotonic expiry)
_tenant_cache: dict[str, tuple] = {}


def clear_tenant_cache() -> None:
    _tenant_cache.clear()


def _mem_get(slug: str):
    entry = _tenant_cache.get(slug)
    if entry and time.monotonic() < entry[1]:
        return True, entry[0]
    return False, None


def _mem_set(slug: str, tenant) -> None:
    _tenant_cache[slug] = (tenant, time.monotonic() + _TENANT_TTL)


def _serialize(tenant: Tenant) -> str:
    return json.dumps({
        "id": str(tenant.id),
        "slug": tenant.slug,
        "name": tenant.name,
        "is_active": tenant.is_active,
        "timezone": tenant.timezone,
        "language": tenant.language,
        "date_format": tenant.date_format,
        "date_pattern": tenant.date_pattern,
    })


def _deserialize(raw: str) -> SimpleNamespace | None:
    data = json.loads(raw)
    if data is None:
        return None
    return SimpleNamespace(
        id=uuid.UUID(data["id"]),
        slug=data["slug"],
        name=data["name"],
        is_active=data["is_active"],
        timezone=data.get("timezone"),
        language=data.get("language"),
        date_format=data.get("date_format"),
        date_pattern=data.get("date_pattern"),
        schema_name=f"tenant_{data['slug']}",
    )


async def _redis_get(client, slug: str):
    raw = await client.get(f"{_REDIS_PREFIX}{slug}")
    if raw is None:
        return False, None
    return True, _deserialize(raw)


async def _redis_set(client, slug: str, tenant) -> None:
    value = _serialize(tenant) if tenant is not None else json.dumps(None)
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


class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not settings.MULTI_TENANT:
            return await call_next(request)

        slug = _extract_slug(request)

        if slug:
            from adminfoundry.redis_client import get_redis
            client = get_redis()

            if client:
                hit, tenant = await _redis_get(client, slug)
            else:
                hit, tenant = _mem_get(slug)

            if not hit:
                async with AsyncSessionLocal() as session:
                    result = await session.execute(
                        select(Tenant).where(Tenant.slug == slug)
                    )
                    tenant = result.scalar_one_or_none()
                if client:
                    await _redis_set(client, slug, tenant)
                else:
                    _mem_set(slug, tenant)

            if tenant is None:
                return JSONResponse(
                    status_code=404,
                    content={"detail": f"Tenant '{slug}' not found"},
                )
            if not tenant.is_active:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Tenant is disabled"},
                )
            request.state.tenant = tenant
        else:
            request.state.tenant = None

        return await call_next(request)
