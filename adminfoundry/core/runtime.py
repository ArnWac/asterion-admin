from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from adminfoundry.authz.registry import PermissionRegistry
from adminfoundry.contract.contributions import ContractContributionRegistry
from adminfoundry.core.config import CoreAdminConfig
from adminfoundry.db.session import DatabaseManager
from adminfoundry.extensions.registry import ExtensionRegistry
from adminfoundry.fields import FieldRegistry, build_default_registry
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
from adminfoundry.ui.navigation import NavigationRegistry


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
    #: Field adapter registry. Default-populated with the built-in scalar
    #: and relation adapters; extensions can prepend custom adapters
    #: during their setup phase. Read by ``schemas/builder.py`` and
    #: ``contract/service.py`` to introspect model columns.
    fields: FieldRegistry = field(default_factory=build_default_registry)
    #: Delivers password-reset links (Roadmap 3.3). Defaults to the
    #: dev-only logging notifier; apps pass a real one to ``create_admin``.
    password_reset_notifier: Any = None
    #: Module-level singleton — every runtime references the same registry.
    #: Extension ``register_protected_fields`` hooks write into it before
    #: ``create_admin`` freezes it for the duration of the request lifecycle.
    protected_fields: ProtectedFieldRegistry = field(
        default_factory=get_protected_field_registry
    )
    #: Extension contributions live in these four registries; populated
    #: during the Phase-5 lifecycle and frozen before the first request.
    extensions: ExtensionRegistry = field(default_factory=ExtensionRegistry)
    permission_registry: PermissionRegistry = field(default_factory=PermissionRegistry)
    contract_contributions: ContractContributionRegistry = field(
        default_factory=ContractContributionRegistry
    )
    navigation: NavigationRegistry = field(default_factory=NavigationRegistry)
    #: ORM model classes contributed by extensions (populated during
    #: ``register_models``). Tooling can iterate this to answer "which
    #: extension owns table X". Table registration itself happens at
    #: class-definition time on the shared :class:`GlobalBase.metadata`.
    extension_models: tuple[type[Any], ...] = field(default_factory=tuple)


def get_runtime(app) -> AdminRuntime:
    return app.state.adminfoundry
