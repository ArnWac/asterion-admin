"""Default :class:`TenantProvider`.

Delegates to the existing :class:`asterion.tenancy.middleware.TenantMiddleware`,
which already resolves the tenant from header/subdomain and stores it on
``request.state.tenant``. This provider simply reads that and normalizes
it to the neutral :class:`AdminTenant` DTO.

External providers can ignore the middleware entirely — e.g. a JWT-claim
provider would parse the active tenant from the access token, without
the middleware ever running.
"""

from __future__ import annotations

from fastapi import Request

from asterion.providers.base import AdminTenant


class BuiltinTenantProvider:
    """Implements :class:`asterion.providers.base.TenantProvider`."""

    async def resolve_tenant(self, request: Request) -> AdminTenant | None:
        ctx = getattr(request.state, "tenant", None)
        if ctx is None:
            return None
        # Carry the resolved schema_name (DB source of truth) so the permission
        # lookup scopes to the same schema the CRUD session uses, instead of
        # re-deriving tenant_<slug>.
        return AdminTenant(
            id=str(ctx.id),
            slug=ctx.slug,
            name=ctx.name,
            schema_name=getattr(ctx, "schema_name", None),
        )
