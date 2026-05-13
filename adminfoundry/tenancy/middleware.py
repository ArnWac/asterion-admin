"""Slim TenantMiddleware that delegates resolution to resolver.py."""
import ipaddress
import json

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from adminfoundry.settings import settings
from adminfoundry.tenancy.resolver import _extract_slug, resolve_tenant


class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not settings.MULTI_TENANT:
            return await call_next(request)

        slug = _extract_slug(request)
        if not slug:
            request.state.tenant = None
            return await call_next(request)

        ctx = await resolve_tenant(request)

        if ctx is None:
            return JSONResponse(
                status_code=404,
                content={"detail": f"Tenant '{slug}' not found"},
            )
        if not ctx.is_active:
            return JSONResponse(
                status_code=403,
                content={"detail": "Tenant is disabled"},
            )
        if ctx.allowed_cidrs:
            client_ip = request.client.host if request.client else None
            if not client_ip:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Access denied: client IP could not be determined"},
                )
            try:
                cidrs = json.loads(ctx.allowed_cidrs)
            except (ValueError, TypeError):
                cidrs = [c.strip() for c in ctx.allowed_cidrs.splitlines() if c.strip()]
            addr = ipaddress.ip_address(client_ip)
            if not any(
                addr in ipaddress.ip_network(c, strict=False)
                for c in cidrs
                if c
            ):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Access denied: IP not in tenant allowlist"},
                )

        request.state.tenant = ctx
        return await call_next(request)
