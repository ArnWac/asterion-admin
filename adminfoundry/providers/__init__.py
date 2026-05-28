"""Provider protocols and default implementations.

A *provider* is a small adapter that abstracts one of the four pluggable
concerns in adminfoundry:

* :class:`~adminfoundry.providers.base.AuthProvider` — turns a request
  into an :class:`~adminfoundry.providers.base.AuthIdentity` (or None).
* :class:`~adminfoundry.providers.base.UserProvider` — turns a user id
  into an :class:`~adminfoundry.providers.base.AdminPrincipal`.
* :class:`~adminfoundry.providers.base.PermissionProvider` — returns a
  user's permission keys and answers superadmin queries.
* :class:`~adminfoundry.providers.base.TenantProvider` — resolves the
  active :class:`~adminfoundry.providers.base.AdminTenant` from the
  request.

Apps that ship with adminfoundry use the four ``Builtin*`` providers,
which wrap the existing JWT/SQLAlchemy/RBAC stack 1:1. Apps with
external identity (Google OAuth, Keycloak, Supabase, …) pass their own
provider instances to :func:`adminfoundry.create_admin` instead.

See :doc:`../docs/architecture` and ``adminfoundry_v1_providers_roadmap.md``
for the migration plan.
"""

from __future__ import annotations

from adminfoundry.providers.auth import BuiltinJWTAuthProvider
from adminfoundry.providers.base import (
    AdminPrincipal,
    AdminTenant,
    AuthIdentity,
    AuthProvider,
    Page,
    PermissionProvider,
    TenantProvider,
    UserListingProvider,
    UserProvider,
    UserQuery,
)
from adminfoundry.providers.permissions import BuiltinPermissionProvider
from adminfoundry.providers.tenants import BuiltinTenantProvider
from adminfoundry.providers.users import BuiltinSQLAlchemyUserProvider

__all__ = [
    "AdminPrincipal",
    "AdminTenant",
    "AuthIdentity",
    "AuthProvider",
    "BuiltinJWTAuthProvider",
    "BuiltinPermissionProvider",
    "BuiltinSQLAlchemyUserProvider",
    "BuiltinTenantProvider",
    "Page",
    "PermissionProvider",
    "TenantProvider",
    "UserListingProvider",
    "UserProvider",
    "UserQuery",
]
