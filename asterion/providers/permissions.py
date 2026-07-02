"""Default :class:`PermissionProvider`.

Wraps the existing tenant-RBAC lookup (``TenantMembership`` →
``TenantMembershipRole`` → ``TenantRole`` → ``TenantRolePermission``)
without changing any of that logic.

Rules implemented here:

* superadmin → effective ``admin.*`` + ``platform.*`` (full access, both
  tiers — see ADR-0004).
* no tenant context + non-superadmin → the caller's ``platform.*`` keys from
  their platform roles (ADR-0004); empty if they hold none, which keeps the
  legacy single-tenant fallback to ``single_tenant_require_superadmin``.
* tenant context + non-superadmin → tenant-local role keys.

The PostgreSQL-only ``SET LOCAL search_path`` quirk for tenant-schema
lookups is preserved exactly as it was in ``require_tenant_auth_context``;
non-postgres backends return the empty set, which is the legacy
behaviour.
"""

from __future__ import annotations

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from asterion.models.platform_rbac import PlatformRole, PlatformUserRole
from asterion.models.tenant_membership import TenantMembership
from asterion.models.tenant_rbac import TenantMembershipRole, TenantRole
from asterion.providers.base import AdminPrincipal, AdminTenant
from asterion.tenancy.schema_strategy import set_search_path


class BuiltinPermissionProvider:
    """Implements :class:`asterion.providers.base.PermissionProvider`."""

    def is_superadmin(self, user: AdminPrincipal) -> bool:
        return user.is_superadmin

    async def get_permissions(
        self,
        user: AdminPrincipal,
        tenant: AdminTenant | None,
        *,
        request: Request | None = None,
    ) -> frozenset[str]:
        if user.is_superadmin:
            # The one place identity becomes keys (ADR-0004). ``admin.*`` lets a
            # superadmin act inside any tenant; ``platform.*`` is the god-mode
            # grant every platform-tier gate authorizes against. A tenant
            # ``owner`` holds only ``admin.*``, so the two stay distinguishable.
            return frozenset({"admin.*", "platform.*"})
        if request is None:
            return frozenset()
        if tenant is None:
            # Shared / no-tenant scope: resolve the caller's *platform* roles
            # (ADR-0004). A plain authenticated user with no platform role gets
            # the empty set, so the no-tenant gate still falls back to
            # ``single_tenant_require_superadmin``. Platform tables are in the
            # public schema, so this works on SQLite too (no search_path).
            return await self._platform_permissions(user, request)

        runtime = request.app.state.asterion
        if "postgresql" not in runtime.config.database_url:
            # Tenant-scoped permission lookup needs SET LOCAL search_path
            # which only works on PostgreSQL — preserves the legacy
            # behaviour where non-postgres tenant deployments return no
            # role-based perms.
            return frozenset()

        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            membership = (
                await session.execute(
                    select(TenantMembership)
                    .where(TenantMembership.user_id == user.id)
                    .where(TenantMembership.tenant_id == tenant.id)
                    .where(TenantMembership.is_active == True)  # noqa: E712
                )
            ).scalar_one_or_none()
            if membership is None:
                return frozenset()

            # Pull tenant-local roles inside the tenant schema.
            await set_search_path(session, _schema_for(tenant))
            roles = (
                (
                    await session.execute(
                        select(TenantRole)
                        .join(
                            TenantMembershipRole,
                            TenantMembershipRole.role_id == TenantRole.id,
                        )
                        .where(TenantMembershipRole.membership_id == membership.id)
                        .options(selectinload(TenantRole.permissions))
                    )
                )
                .scalars()
                .all()
            )

        keys: set[str] = set()
        for role in roles:
            for perm in role.permissions:
                keys.add(perm.permission_key)
        return frozenset(keys)

    async def _platform_permissions(
        self, user: AdminPrincipal, request: Request
    ) -> frozenset[str]:
        """Resolve a shared-scope caller's ``platform.*`` keys from their
        platform roles (ADR-0004). Public-schema lookup — no tenant, no
        search_path — so it runs on any backend."""
        runtime = request.app.state.asterion
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            roles = (
                (
                    await session.execute(
                        select(PlatformRole)
                        .join(
                            PlatformUserRole,
                            PlatformUserRole.role_id == PlatformRole.id,
                        )
                        .where(PlatformUserRole.user_id == user.id)
                        .options(selectinload(PlatformRole.permissions))
                    )
                )
                .scalars()
                .all()
            )

        keys: set[str] = set()
        for role in roles:
            for perm in role.permissions:
                keys.add(perm.permission_key)
        return frozenset(keys)


def _schema_for(tenant: AdminTenant) -> str:
    """Resolve the tenant's PostgreSQL schema for the RBAC lookup.

    Prefer the provider-supplied ``schema_name`` (the DB source of truth, also
    used by the request CRUD session) so both read the SAME schema; fall back
    to the ``tenant_<slug>`` convention for providers that don't supply it.
    """
    if tenant.schema_name:
        return tenant.schema_name
    from asterion.tenancy.schema_names import make_tenant_schema_name

    return make_tenant_schema_name(tenant.slug)
