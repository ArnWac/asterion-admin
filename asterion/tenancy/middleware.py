"""Slim TenantMiddleware that delegates resolution to resolver.py.

Error responses go through the consistent envelope helper so a
tenant-resolution failure looks exactly like every other API error from
the client's point of view.
"""

import ipaddress
import json

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from asterion.core.errors import FORBIDDEN, NOT_FOUND, error_response
from asterion.core.net import request_client_ip
from asterion.tenancy.resolver import _extract_slug, resolve_tenant


class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        slug = _extract_slug(request)
        if not slug:
            request.state.tenant = None
            return await call_next(request)

        ctx = await resolve_tenant(request)

        if ctx is None:
            return error_response(
                request,
                status_code=404,
                code=NOT_FOUND,
                message=f"Tenant {slug!r} not found.",
            )
        if not ctx.is_active:
            return error_response(
                request,
                status_code=403,
                code=FORBIDDEN,
                message=f"Tenant {slug!r} is disabled.",
            )
        if ctx.allowed_cidrs:
            client_ip = request_client_ip(request)
            if not client_ip:
                return error_response(
                    request,
                    status_code=403,
                    code=FORBIDDEN,
                    message="Access denied: client IP could not be determined.",
                )
            try:
                cidrs = json.loads(ctx.allowed_cidrs)
            except (ValueError, TypeError):
                cidrs = [c.strip() for c in ctx.allowed_cidrs.splitlines() if c.strip()]
            addr = ipaddress.ip_address(client_ip)
            if not any(addr in ipaddress.ip_network(c, strict=False) for c in cidrs if c):
                return error_response(
                    request,
                    status_code=403,
                    code=FORBIDDEN,
                    message="Access denied: IP not in tenant allowlist.",
                )

        request.state.tenant = ctx
        return await call_next(request)
