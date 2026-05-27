from __future__ import annotations

import logging
from collections.abc import Callable, Iterable

from fastapi import FastAPI

from adminfoundry.builtins import install_builtin_admins
from adminfoundry.core.config import CoreAdminConfig
from adminfoundry.core.errors import register_error_handlers
from adminfoundry.core.installers import install_middleware, install_routes
from adminfoundry.core.logging import configure_logging
from adminfoundry.core.runtime import AdminRuntime, ProviderSet
from adminfoundry.db.session import DatabaseManager
from adminfoundry.extensions import AdminExtension, ExtensionContext
from adminfoundry.extensions.lifecycle import compose_lifespan, run_setup_phase
from adminfoundry.providers import (
    BuiltinJWTAuthProvider,
    BuiltinPermissionProvider,
    BuiltinSQLAlchemyUserProvider,
    BuiltinTenantProvider,
)
from adminfoundry.providers.base import (
    AuthProvider,
    PermissionProvider,
    TenantProvider,
    UserProvider,
)
from adminfoundry.registry import AdminRegistry


def create_admin(
    config: CoreAdminConfig | None = None,
    *,
    register: Callable[[AdminRegistry], None] | None = None,
    extensions: Iterable[AdminExtension] = (),
    auth_provider: AuthProvider | None = None,
    user_provider: UserProvider | None = None,
    permission_provider: PermissionProvider | None = None,
    tenant_provider: TenantProvider | None = None,
    **fastapi_kwargs,
) -> FastAPI:
    config = config or CoreAdminConfig.from_env()
    config.validate()

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

    runtime = AdminRuntime(
        config=config,
        db=DatabaseManager(
            config.database_url,
            echo=config.debug,
            pool_size=config.db_pool_size,
            max_overflow=config.db_max_overflow,
            pool_pre_ping=config.db_pool_pre_ping,
        ),
        providers=providers,
    )

    # Register extensions up front so the lifespan composer can see them.
    runtime.extensions.register_all(extensions)

    composed = compose_lifespan(runtime.extensions, user_lifespan)

    app = FastAPI(
        title=config.app_title,
        debug=config.debug,
        lifespan=composed,
        **fastapi_kwargs,
    )
    app.state.adminfoundry = runtime

    register_error_handlers(app)
    install_middleware(app, config)

    if config.enable_builtin_admins:
        install_builtin_admins(runtime.registry)

    if register is not None:
        register(runtime.registry)

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
        logger=logging.getLogger("adminfoundry.extensions"),
    )
    runtime.extension_models = run_setup_phase(runtime.extensions, ctx, app)

    # Freeze the admin registry now that every setup-time contributor
    # (builtin admins, the user's ``register=`` callback, extensions)
    # has had its turn. Anything that tries to mutate the registry from
    # a request handler will surface a RegistryFrozenError instead of
    # silently breaking cached contracts / route tables.
    runtime.registry.freeze()

    install_routes(app, config)

    return app
