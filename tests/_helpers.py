"""Shared test helpers for the v1-providers migration.

Most existing test fixtures grant authorization via FastAPI dependency
overrides on ``require_tenant_auth_context``. Phase 2 of the v1-providers
refactor moved every CRUD/contract/actions/import-export router off that
dependency and onto :func:`asterion.admin.require_admin_context`, so
those overrides became dead code.

:func:`override_admin_context` is the new one-stop helper — install it
once per app fixture and the framework's authenticated routes see exactly
the principal / tenant / permissions you pass.
"""

from __future__ import annotations

import uuid
from typing import Any

from asterion.admin import AdminContext, require_admin_context
from asterion.providers.base import AdminPrincipal, AdminTenant


def make_admin_principal(
    *,
    id: str | None = None,
    email: str = "test-user@example.com",
    is_superadmin: bool = False,
) -> AdminPrincipal:
    return AdminPrincipal(
        id=id or "11111111-1111-1111-1111-111111111111",
        email=email,
        display_name="Test User",
        is_active=True,
        is_superadmin=is_superadmin,
    )


def make_admin_tenant(slug: str = "acme") -> AdminTenant:
    return AdminTenant(id=str(uuid.uuid4()), slug=slug, name=slug.capitalize())


def override_admin_context(
    app: Any,
    *,
    principal: AdminPrincipal | None = None,
    tenant: AdminTenant | None = None,
    permissions: set[str] | frozenset[str] = frozenset(),
    roles: set[str] | frozenset[str] = frozenset(),
) -> None:
    """Inject an :class:`AdminContext` into ``app`` for the duration of a test.

    Defaults give an authenticated **superadmin** principal with no tenant and
    no permissions — the authorized caller for the no-tenant (single-tenant)
    admin surface, which now requires superadmin
    (``single_tenant_require_superadmin``). Pass ``tenant=…`` (+ ``permissions``)
    to exercise the per-resource permission-key path, or
    ``principal=make_admin_principal(is_superadmin=False)`` to assert the
    single-tenant 403.
    """
    effective_principal = (
        principal if principal is not None else make_admin_principal(is_superadmin=True)
    )

    async def _override() -> AdminContext:
        return AdminContext(
            request=None,
            principal=effective_principal,
            tenant=tenant,
            permissions=frozenset(permissions),
            roles=frozenset(roles),
        )

    app.dependency_overrides[require_admin_context] = _override
