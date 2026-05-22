from __future__ import annotations

from dataclasses import dataclass, field

from adminfoundry.core.config import CoreAdminConfig
from adminfoundry.db.session import DatabaseManager
from adminfoundry.providers.base import (
    AuthProvider,
    PermissionProvider,
    TenantProvider,
    UserProvider,
)
from adminfoundry.registry import AdminRegistry
from adminfoundry.security.protected_fields import (
    ProtectedFieldRegistry,
)
from adminfoundry.security.protected_fields import (
    get_registry as get_protected_field_registry,
)


@dataclass(slots=True)
class ProviderSet:
    """Container for the four pluggable providers.

    Lives on :class:`AdminRuntime` so request-scoped dependencies can
    reach the active providers via ``request.app.state.adminfoundry.providers``.
    """

    auth: AuthProvider
    users: UserProvider
    permissions: PermissionProvider
    tenants: TenantProvider


def _default_providers() -> ProviderSet:
    # Imported lazily so adminfoundry.core.runtime stays importable from
    # places that don't pull in the provider defaults (e.g. type checkers
    # and pure-DTO consumers).
    from adminfoundry.providers import (
        BuiltinJWTAuthProvider,
        BuiltinPermissionProvider,
        BuiltinSQLAlchemyUserProvider,
        BuiltinTenantProvider,
    )

    return ProviderSet(
        auth=BuiltinJWTAuthProvider(),
        users=BuiltinSQLAlchemyUserProvider(),
        permissions=BuiltinPermissionProvider(),
        tenants=BuiltinTenantProvider(),
    )


@dataclass(slots=True)
class AdminRuntime:
    config: CoreAdminConfig
    db: DatabaseManager
    registry: AdminRegistry = field(default_factory=AdminRegistry)
    providers: ProviderSet = field(default_factory=_default_providers)
    #: Module-level singleton — every runtime references the same registry.
    #: Phase 5 extensions register via ``runtime.protected_fields.register(...)``;
    #: ``create_admin`` freezes the registry after extension setup.
    protected_fields: ProtectedFieldRegistry = field(
        default_factory=get_protected_field_registry
    )


def get_runtime(app) -> AdminRuntime:
    return app.state.adminfoundry
