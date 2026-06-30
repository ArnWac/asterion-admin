from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from asterion.authz.registry import PermissionRegistry
from asterion.contract.contributions import ContractContributionRegistry
from asterion.core.config import CoreAdminConfig
from asterion.db.session import DatabaseManager
from asterion.extensions.registry import ExtensionRegistry
from asterion.fields import FieldRegistry, build_default_registry
from asterion.notifications.base import NotifierRegistry
from asterion.providers.base import (
    AuthProvider,
    PermissionProvider,
    TenantProvider,
    UserProvider,
)
from asterion.registry import AdminRegistry
from asterion.security.protected_fields import (
    ProtectedFieldRegistry,
)
from asterion.security.protected_fields import (
    get_registry as get_protected_field_registry,
)
from asterion.ui.admin_pages import AdminPageRegistry
from asterion.ui.navigation import NavigationRegistry


@dataclass(slots=True)
class ProviderSet:
    """Container for the four pluggable providers.

    Lives on :class:`AdminRuntime` so request-scoped dependencies can
    reach the active providers via ``request.app.state.asterion.providers``.
    """

    auth: AuthProvider
    users: UserProvider
    permissions: PermissionProvider
    tenants: TenantProvider


def _default_providers() -> ProviderSet:
    # Imported lazily so asterion.core.runtime stays importable from
    # places that don't pull in the provider defaults (e.g. type checkers
    # and pure-DTO consumers).
    from asterion.providers import (
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
    #: Also auto-registered into :attr:`notifiers` so generic lookups
    #: (Roadmap P4.5) find it.
    password_reset_notifier: Any = None
    #: Delivers member-invite links (tenant member-management). Defaults to
    #: the dev-only logging notifier; apps pass a real one to
    #: ``create_admin``. Also auto-registered into :attr:`notifiers`.
    invite_notifier: Any = None
    #: Login rate-limiter backend (Review R7). ``None`` → the auth router
    #: falls back to its in-memory, single-process default. Multi-worker
    #: deployments pass a shared backend (e.g.
    #: ``asterion.extensions.rate_limit_redis.RedisLoginRateLimiter``)
    #: satisfying :class:`asterion.auth.rate_limiter.RateLimiterBackend`.
    login_rate_limiter: Any = None
    #: Password-reset request rate-limiter (separate counter from the login
    #: limiter). ``create_admin`` builds an in-process default from
    #: ``CoreAdminConfig.password_reset_rate_limit_*``; a multi-worker
    #: deployment can replace it with a shared backend satisfying
    #: :class:`asterion.auth.rate_limiter.RateLimiterBackend`.
    password_reset_rate_limiter: Any = None
    #: Password acceptance policy (G21). ``create_admin`` builds a
    #: :class:`asterion.auth.password_policy.DefaultPasswordPolicy` from
    #: ``CoreAdminConfig.password_min_length`` / ``password_hibp_check``; an app
    #: can replace it with any object satisfying
    #: :class:`asterion.auth.password_policy.PasswordPolicy`.
    password_policy: Any = None
    #: Typed-notifier registry (Roadmap P4.5). Apps and extensions
    #: register implementations keyed by their Protocol type; publishers
    #: look them up with :meth:`NotifierRegistry.get` and treat ``None``
    #: as "no notifier configured for this event".
    notifiers: NotifierRegistry = field(default_factory=NotifierRegistry)
    #: Storage backend for :class:`FileField` (Roadmap P4). ``None``
    #: when neither ``CoreAdminConfig.storage_root`` nor an explicit
    #: ``storage=`` were passed to ``create_admin`` — apps that don't
    #: use file fields don't need to configure one. Accessing this on
    #: the request path while ``None`` is a clear programming error;
    #: the FileField adapter / upload router check it and raise.
    storage: Any = None
    #: Module-level singleton — every runtime references the SAME registry.
    #: This is deliberate and documented (see
    #: ``asterion/security/protected_fields.py`` and the "documented
    #: singletons are allowed" carve-out in
    #: ``docs/roadmap.md``): a leaked
    #: secret from any admin's response is the same security failure no
    #: matter which app instance holds the registry, so protected fields
    #: are a global, fail-safe concern — two apps sharing the registry can
    #: only ever *over*-protect, never leak. The read path
    #: (``ModelAdmin.all_protected`` / inline serialization) reads this
    #: same singleton, so do NOT swap this for a per-runtime factory
    #: without also re-routing every reader — see
    #: ``tests/security/test_protected_field_registry.py``. Extension
    #: ``register_protected_fields`` hooks write into it before
    #: ``create_admin`` freezes it for the duration of the request lifecycle.
    protected_fields: ProtectedFieldRegistry = field(default_factory=get_protected_field_registry)
    #: Extension contributions live in these four registries; populated
    #: during the Phase-5 lifecycle and frozen before the first request.
    extensions: ExtensionRegistry = field(default_factory=ExtensionRegistry)
    permission_registry: PermissionRegistry = field(default_factory=PermissionRegistry)
    contract_contributions: ContractContributionRegistry = field(
        default_factory=ContractContributionRegistry
    )
    navigation: NavigationRegistry = field(default_factory=NavigationRegistry)
    #: Pluggable admin pages (Roadmap 5.6). Apps + extensions register
    #: ``AdminPage`` entries; the framework mounts a UI route per page
    #: and mirrors them into ``navigation`` so they show up in the
    #: sidebar (gated by the page's optional ``permission``).
    admin_pages: AdminPageRegistry = field(default_factory=AdminPageRegistry)
    #: ORM model classes contributed by extensions (populated during
    #: ``register_models``). Tooling can iterate this to answer "which
    #: extension owns table X". Table registration itself happens at
    #: class-definition time on the shared :class:`GlobalBase.metadata`.
    extension_models: tuple[type[Any], ...] = field(default_factory=tuple)


def get_runtime(app) -> AdminRuntime:
    return app.state.asterion
