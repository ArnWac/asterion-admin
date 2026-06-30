from __future__ import annotations

import logging
from collections.abc import Callable, Iterable

from fastapi import FastAPI

from asterion.auth.password_policy import DefaultPasswordPolicy
from asterion.auth.rate_limiter import InMemoryLoginRateLimiter
from asterion.authz.registry import PermissionRegistry
from asterion.builtins import install_builtin_admins
from asterion.core.config import CoreAdminConfig
from asterion.core.errors import register_error_handlers
from asterion.core.installers import install_middleware, install_routes
from asterion.core.logging import configure_logging
from asterion.core.observability import build_observability
from asterion.core.runtime import AdminRuntime, ProviderSet
from asterion.db.session import DatabaseManager
from asterion.extensions import AdminExtension, ExtensionContext
from asterion.extensions.lifecycle import compose_lifespan, run_setup_phase
from asterion.privacy.redaction import (
    set_default_audit_pii_mode,
    set_default_behavioral_detail,
)
from asterion.providers import (
    BuiltinJWTAuthProvider,
    BuiltinPermissionProvider,
    BuiltinSQLAlchemyUserProvider,
    BuiltinTenantProvider,
)
from asterion.providers.base import (
    AuthProvider,
    PermissionProvider,
    TenantProvider,
    UserProvider,
)
from asterion.registry import AdminRegistry


def create_admin(
    config: CoreAdminConfig | None = None,
    *,
    register: Callable[[AdminRegistry], None] | None = None,
    extensions: Iterable[AdminExtension] = (),
    auth_provider: AuthProvider | None = None,
    user_provider: UserProvider | None = None,
    permission_provider: PermissionProvider | None = None,
    tenant_provider: TenantProvider | None = None,
    password_reset_notifier=None,
    invite_notifier=None,
    storage=None,
    login_rate_limiter=None,
    permissions: Iterable[str] | Callable[[PermissionRegistry], None] | None = None,
    **fastapi_kwargs,
) -> FastAPI:
    config = config or CoreAdminConfig.from_env()
    config.validate()

    # Publish the process-wide audit PII-redaction mode (G7) + behavioural-detail
    # policy (G5) so the audit writer's many call sites don't each need the config
    # threaded through. Mirrors the framework's other cross-cutting singletons
    # (PII / protected fields). Secure defaults ("redact" + suppress) already
    # apply before this runs.
    set_default_audit_pii_mode(config.audit_pii_mode)
    set_default_behavioral_detail(config.audit_behavioral_detail)

    # §9: external user-mode must carry at least an explicit
    # ``auth_provider`` — otherwise the framework would silently fall
    # back to the builtin JWT provider, which is the opposite of what
    # the operator declared. user_provider often follows naturally;
    # we require auth_provider as the smallest "you really meant it"
    # gate. Permission + tenant providers can stay builtin in external
    # mode for staged migrations.
    if config.user_mode == "external" and auth_provider is None:
        raise ValueError(
            "user_mode='external' requires an explicit auth_provider on "
            "create_admin(). Pass your own AuthProvider implementation or "
            "switch user_mode back to 'builtin' for the framework's JWT stack."
        )

    configure_logging(config)

    # The user may have passed their own lifespan. We compose it with the
    # extension startup/shutdown hooks so both run, in the right order.
    user_lifespan = fastapi_kwargs.pop("lifespan", None)

    # Each provider defaults to the framework's built-in implementation,
    # which preserves v1 behaviour exactly. Apps with external identity
    # pass their own implementations here.
    providers = ProviderSet(
        auth=auth_provider or BuiltinJWTAuthProvider(),
        users=user_provider or BuiltinSQLAlchemyUserProvider(),
        permissions=permission_provider or BuiltinPermissionProvider(),
        tenants=tenant_provider or BuiltinTenantProvider(),
    )

    # Password-reset delivery (Roadmap 3.3). Defaults to the dev-only
    # logging notifier; production apps pass a real email sender.
    if password_reset_notifier is None:
        from asterion.auth.password_reset import LoggingPasswordResetNotifier

        password_reset_notifier = LoggingPasswordResetNotifier()

    # Member-invite delivery (tenant member-management). Same dev-default /
    # production-override split as the password-reset notifier.
    if invite_notifier is None:
        from asterion.auth.invite import LoggingInviteNotifier

        invite_notifier = LoggingInviteNotifier()

    # Storage backend (Roadmap P4). Three paths:
    #   1. explicit ``storage=`` wins (e.g. S3 from an extension)
    #   2. ``CoreAdminConfig.storage_root`` set → auto-wire LocalFileStorage
    #   3. neither → ``runtime.storage`` stays None; FileField raises a
    #      clear error if anything tries to use it
    if storage is None and config.storage_root:
        from asterion.storage import LocalFileStorage

        storage = LocalFileStorage(config.storage_root)

    runtime = AdminRuntime(
        config=config,
        db=DatabaseManager(
            config.database_url,
            echo=config.debug,
            pool_size=config.db_pool_size,
            max_overflow=config.db_max_overflow,
            pool_pre_ping=config.db_pool_pre_ping,
            statement_cache_size=config.resolved_statement_cache_size(),
        ),
        providers=providers,
        password_reset_notifier=password_reset_notifier,
        invite_notifier=invite_notifier,
        storage=storage,
        login_rate_limiter=login_rate_limiter,
        password_reset_rate_limiter=InMemoryLoginRateLimiter(
            max_failures=config.password_reset_rate_limit_max,
            window_seconds=config.password_reset_rate_limit_window_seconds,
        ),
        password_policy=DefaultPasswordPolicy(
            min_length=config.password_min_length,
            hibp_check=config.password_hibp_check,
            hibp_timeout=config.password_hibp_timeout_seconds,
        ),
        tenant_rate_limiter=InMemoryLoginRateLimiter(
            max_failures=config.tenant_rate_limit_max,
            window_seconds=config.tenant_rate_limit_window_seconds,
        ),
        observability=build_observability(
            enabled=config.observability_enabled,
            service_name=config.app_title,
        ),
    )

    # Mirror the explicit ``password_reset_notifier`` into the generic
    # notifier registry (P4.5) so publishers that look up by Protocol
    # type find it without depending on the ad-hoc keyword. The
    # explicit attribute stays for backwards compat.
    from asterion.auth.password_reset import PasswordResetNotifier

    runtime.notifiers.register(PasswordResetNotifier, password_reset_notifier)

    from asterion.auth.invite import InviteNotifier

    runtime.notifiers.register(InviteNotifier, invite_notifier)

    # Register extensions up front so the lifespan composer can see them.
    runtime.extensions.register_all(extensions)

    composed = compose_lifespan(runtime.extensions, user_lifespan)

    app = FastAPI(
        title=config.app_title,
        debug=config.debug,
        lifespan=composed,
        **fastapi_kwargs,
    )
    app.state.asterion = runtime

    register_error_handlers(app)
    install_middleware(app, config)

    if config.enable_builtin_admins:
        install_builtin_admins(runtime.registry)

    if register is not None:
        register(runtime.registry)

    # App-declared permission keys — the extension-free path for an embedding
    # app (e.g. Simpletimes) to publish its own keys without writing an
    # AdminExtension. Registered into the permission registry BEFORE
    # ``run_setup_phase`` freezes it, so they merge with extension-registered
    # keys and flow through ``generate_permission_keys()`` into the catalog on
    # ``asterion permissions sync``. Order: app keys first, then each
    # extension's ``register_permissions`` hook. Duplicates are idempotent
    # (the registry is a set), so a key declared by both app and extension is
    # fine; ``PermissionRegistry.register`` validates the key shape and raises
    # on a malformed key.
    if permissions is not None:
        if callable(permissions):
            permissions(runtime.permission_registry)
        else:
            runtime.permission_registry.register(*permissions)

    # Build the per-app ExtensionContext and walk every extension through
    # the documented lifecycle hooks. Extension routes are mounted INSIDE
    # this call (before install_routes below), so static-path extension
    # routes win over the dynamic CRUD /{resource}/{id} route.
    ctx = ExtensionContext(
        config=config,
        permissions=runtime.permission_registry,
        contract=runtime.contract_contributions,
        navigation=runtime.navigation,
        protected_fields=runtime.protected_fields,
        admin_pages=runtime.admin_pages,
        logger=logging.getLogger("asterion.extensions"),
    )
    # run_setup_phase walks every extension through its hooks (including
    # register_admin_pages), mirrors permission-bearing admin pages into
    # navigation, and freezes the extension-side registries — navigation
    # and admin_pages included. See asterion/extensions/lifecycle.py.
    runtime.extension_models = run_setup_phase(runtime.extensions, ctx, app)

    # Freeze the admin registry now that every setup-time contributor
    # (builtin admins, the user's ``register=`` callback, extensions)
    # has had its turn. Anything that tries to mutate the registry from
    # a request handler will surface a RegistryFrozenError instead of
    # silently breaking cached contracts / route tables.
    runtime.registry.freeze()

    install_routes(app, config)

    return app
