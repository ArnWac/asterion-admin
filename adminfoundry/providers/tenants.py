"""Default :class:`TenantProvider`.

Delegates to the existing :class:`adminfoundry.tenancy.middleware.TenantMiddleware`,
which already resolves the tenant from header/subdomain and stores it on
``request.state.tenant``. This provider simply reads that and normalizes
it to the neutral :class:`AdminTenant` DTO.

External providers can ignore the middleware entirely — e.g. a JWT-claim
provider would parse the active tenant from the access token, without
the middleware ever running.
"""

from __future__ import annotations

from fastapi import Request

from adminfoundry.providers.base import AdminTenant


class BuiltinTenantProvider:
    """Implements :class:`adminfoundry.providers.base.TenantProvider`."""

    async def resolve_tenant(self, request: Request) -> AdminTenant | None:
        ctx = getattr(request.state, "tenant", None)
        if ctx is None:
            return None
        return AdminTenant(id=str(ctx.id), slug=ctx.slug, name=ctx.name)
