"""Shared test helpers for the v1-providers migration.

Most existing test fixtures grant authorization via FastAPI dependency
overrides on ``require_tenant_auth_context``. Phase 2 of the v1-providers
refactor moved every CRUD/contract/actions/import-export router off that
dependency and onto :func:`adminfoundry.admin.require_admin_context`, so
those overrides became dead code.

:func:`override_admin_context` is the new one-stop helper — install it
once per app fixture and the framework's authenticated routes see exactly
the user / tenant / permissions you pass.
"""

from __future__ import annotations

import uuid
from typing import Any

from adminfoundry.admin import AdminContext, require_admin_context
from adminfoundry.providers.base import AdminTenant, AdminUser


def make_admin_user(
    *,
    id: str | None = None,
    email: str = "test-user@example.com",
    is_superadmin: bool = False,
) -> AdminUser:
    return AdminUser(
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
    user: AdminUser | None = None,
    tenant: AdminTenant | None = None,
    permissions: set[str] | frozenset[str] = frozenset(),
) -> None:
    """Inject an :class:`AdminContext` into ``app`` for the duration of a test.

    Defaults give an authenticated non-superadmin user with no tenant and no
    permissions, which is enough to clear the auth gate on every migrated
    router. Pass ``tenant=…`` to exercise the permission keys path.
    """
    effective_user = user if user is not None else make_admin_user()

    async def _override() -> AdminContext:
        return AdminContext(
            request=None,
            user=effective_user,
            tenant=tenant,
            permissions=frozenset(permissions),
        )

    app.dependency_overrides[require_admin_context] = _override
