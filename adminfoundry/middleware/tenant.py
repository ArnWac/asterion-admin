import time
from fastapi.responses import JSONResponse
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from adminfoundry.database import AsyncSessionLocal
from adminfoundry.models.tenant import Tenant
from adminfoundry.schemas.tenant import RESERVED_SLUGS
from adminfoundry.settings import settings

_TENANT_TTL = 30  # seconds — short enough to pick up disable/delete quickly
# slug → (Tenant | None, monotonic expiry)
_tenant_cache: dict[str, tuple] = {}


def clear_tenant_cache() -> None:
    _tenant_cache.clear()


def _cache_get(slug: str):
    entry = _tenant_cache.get(slug)
    if entry and time.monotonic() < entry[1]:
        return True, entry[0]
    return False, None


def _cache_set(slug: str, tenant) -> None:
    _tenant_cache[slug] = (tenant, time.monotonic() + _TENANT_TTL)


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
            hit, tenant = _cache_get(slug)
            if not hit:
                async with AsyncSessionLocal() as session:
                    result = await session.execute(
                        select(Tenant).where(Tenant.slug == slug)
                    )
                    tenant = result.scalar_one_or_none()
                _cache_set(slug, tenant)

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
