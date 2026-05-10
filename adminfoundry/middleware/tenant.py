from fastapi.responses import JSONResponse
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from adminfoundry.database import AsyncSessionLocal
from adminfoundry.models.tenant import Tenant
from adminfoundry.schemas.tenant import RESERVED_SLUGS
from adminfoundry.settings import settings


def _extract_slug(request: Request) -> str | None:
    if settings.TENANT_RESOLUTION_STRATEGY == "subdomain":
        host = request.headers.get("host", "").split(":")[0]  # strip port
        parts = host.split(".")
        # acme.localhost → ["acme", "localhost"] — works for local dev
        # acme.example.com → ["acme", "example", "com"]
        # localhost → ["localhost"] — no subdomain, skip
        if len(parts) >= 2:
            candidate = parts[0]
            if candidate not in RESERVED_SLUGS:
                return candidate
        return None
    # default: header
    return request.headers.get("X-Tenant-Slug")


class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not settings.MULTI_TENANT:
            return await call_next(request)

        slug = _extract_slug(request)

        if slug:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Tenant).where(Tenant.slug == slug)
                )
                tenant = result.scalar_one_or_none()

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
