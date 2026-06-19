"""Default :class:`PermissionProvider`.

Wraps the existing tenant-RBAC lookup (``TenantMembership`` →
``TenantMembershipRole`` → ``TenantRole`` → ``TenantRolePermission``)
without changing any of that logic.

Rules implemented here:

* superadmin → effective ``admin.*`` (full access).
* no tenant context → empty set (matches v1 single-tenant behaviour
  where non-superadmin requests aren't gated by role keys at all).
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
            return frozenset({"admin.*"})
        if tenant is None or request is None:
            return frozenset()

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
