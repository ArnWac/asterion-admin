from __future__ import annotations

from collections.abc import Callable, Iterable

from fastapi import FastAPI

from adminfoundry.builtins import install_builtin_admins
from adminfoundry.core.config import CoreAdminConfig
from adminfoundry.core.errors import register_error_handlers
from adminfoundry.core.installers import install_middleware, install_routes
from adminfoundry.core.logging import configure_logging
from adminfoundry.core.runtime import AdminRuntime, ProviderSet
from adminfoundry.db.session import DatabaseManager
from adminfoundry.extensions import Extension
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
    extensions: Iterable[Extension] = (),
    auth_provider: AuthProvider | None = None,
    user_provider: UserProvider | None = None,
    permission_provider: PermissionProvider | None = None,
    tenant_provider: TenantProvider | None = None,
    **fastapi_kwargs,
) -> FastAPI:
    config = config or CoreAdminConfig.from_env()
    config.validate()

    configure_logging(config)

    app = FastAPI(
        title=config.app_title,
        debug=config.debug,
        **fastapi_kwargs,
    )

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

    app.state.adminfoundry = runtime

    register_error_handlers(app)
    install_middleware(app, config)

    if config.enable_builtin_admins:
        install_builtin_admins(runtime.registry)

    if register is not None:
        register(runtime.registry)

    # Extensions run AFTER user registration and BEFORE core routes are
    # mounted, so an extension's static routes (e.g. /{resource}/_export)
    # are matched ahead of the dynamic CRUD /{resource}/{id} route.
    for extension in extensions:
        extension(runtime.registry, app, config)

    install_routes(app, config)

    return app
