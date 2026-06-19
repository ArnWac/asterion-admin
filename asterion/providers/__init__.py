"""Provider protocols and default implementations.

A *provider* is a small adapter that abstracts one of the four pluggable
concerns in asterion:

* :class:`~asterion.providers.base.AuthProvider` — turns a request
  into an :class:`~asterion.providers.base.AuthIdentity` (or None).
* :class:`~asterion.providers.base.UserProvider` — turns a user id
  into an :class:`~asterion.providers.base.AdminPrincipal`.
* :class:`~asterion.providers.base.PermissionProvider` — returns a
  user's permission keys and answers superadmin queries.
* :class:`~asterion.providers.base.TenantProvider` — resolves the
  active :class:`~asterion.providers.base.AdminTenant` from the
  request.

Apps that ship with asterion use the four ``Builtin*`` providers,
which wrap the existing JWT/SQLAlchemy/RBAC stack 1:1. Apps with
external identity (Google OAuth, Keycloak, Supabase, …) pass their own
provider instances to :func:`asterion.create_admin` instead.

See :doc:`../docs/architecture` and ``asterion_v1_providers_roadmap.md``
for the migration plan.
"""

from __future__ import annotations

from asterion.providers.auth import BuiltinJWTAuthProvider
from asterion.providers.base import (
    AdminPrincipal,
    AdminTenant,
    AuthIdentity,
    AuthProvider,
    AuthSession,
    CredentialAuthProvider,
    LoginCredentials,
    LoginError,
    Page,
    PermissionProvider,
    TenantProvider,
    UserListingProvider,
    UserProvider,
    UserQuery,
)
from asterion.providers.permissions import BuiltinPermissionProvider
from asterion.providers.tenants import BuiltinTenantProvider
from asterion.providers.users import BuiltinSQLAlchemyUserProvider

__all__ = [
    "AdminPrincipal",
    "AdminTenant",
    "AuthIdentity",
    "AuthProvider",
    "AuthSession",
    "BuiltinJWTAuthProvider",
    "BuiltinPermissionProvider",
    "BuiltinSQLAlchemyUserProvider",
    "BuiltinTenantProvider",
    "CredentialAuthProvider",
    "LoginCredentials",
    "LoginError",
    "Page",
    "PermissionProvider",
    "TenantProvider",
    "UserListingProvider",
    "UserProvider",
    "UserQuery",
]
