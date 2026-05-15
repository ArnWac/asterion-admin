"""Admin router aggregator — assembles sub-route modules and owns the app factory.

Route registration order matters: fixed-path routes (contract, dashboard, profile,
preferences, permissions) must be included before the parameterized CRUD catch-all routes.
"""
from fastapi import APIRouter
from adminfoundry.settings import settings  # noqa: F401 — re-exported; tests patch adminfoundry.admin.router.settings

# ---------------------------------------------------------------------------
# Main router — kept at this module path for backward compatibility.
# Tests and internal code reference adminfoundry.admin.router.router.
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Registry overview — empty path route must live on the prefixed router directly;
# FastAPI rejects include_router when both prefix and path are empty.
# ---------------------------------------------------------------------------
from fastapi import Depends, HTTPException, Request  # noqa: E402
from adminfoundry.admin.registry import admin_site as _admin_site  # noqa: E402
from adminfoundry.dependencies import get_current_user as _get_current_user  # noqa: E402
from adminfoundry.models.user import User as _User  # noqa: E402


@router.get("")
async def list_registered_models(
    request: Request,
    current_user: _User = Depends(_get_current_user),
):
    """Return registry metadata filtered for the current panel context."""
    payload = getattr(request.state, "token_payload", {})
    tenant = getattr(request.state, "tenant", None)
    is_impersonating = bool(payload.get("impersonated_by"))
    in_tenant_context = is_impersonating or tenant is not None

    if not in_tenant_context:
        from adminfoundry.auth_provider import AuthProvider
        provider = getattr(request.app.state, "auth_provider", AuthProvider())
        if not provider.is_superadmin(current_user):
            raise HTTPException(status_code=403, detail="Superadmin required")

    all_meta = _admin_site.metadata()
    if in_tenant_context:
        models = [
            m for m in all_meta
            if getattr(_admin_site.get(m["model"]), "tenant_scoped", False)
        ]
    else:
        models = [
            m for m in all_meta
            if not getattr(_admin_site.get(m["model"]), "tenant_scoped", False)
            or getattr(_admin_site.get(m["model"]), "global_only_in_root_panel", False)
        ]
    return {"models": models}


from adminfoundry.admin.routes import contract, dashboard, profile, preferences, permissions, crud  # noqa: E402
router.include_router(contract.router)
router.include_router(dashboard.router)
router.include_router(profile.router)
router.include_router(preferences.router)
router.include_router(permissions.router)
router.include_router(crud.router)

__all__ = [
    "router",
    "create_admin",
    "settings",
    "_admin_config",
]

# ---------------------------------------------------------------------------
# Module-level state — set by create_admin(); None until wired.
# External code accesses these via `import adminfoundry.admin.router as r; r._admin_config`.
# ---------------------------------------------------------------------------
_admin_config = None


# ---------------------------------------------------------------------------
# App factory helpers
# ---------------------------------------------------------------------------

def _make_lifespan(user_lifespan, enable_cleanup: bool, cleanup_interval: int):
    """Return a lifespan that optionally composes periodic cleanup with user_lifespan."""
    if not enable_cleanup:
        return user_lifespan

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _cleanup_ctx(app):
        import asyncio
        from adminfoundry.cleanup import periodic_cleanup
        task = asyncio.create_task(periodic_cleanup(interval_seconds=cleanup_interval))
        try:
            yield
        finally:
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

    if user_lifespan is None:
        return _cleanup_ctx

    @asynccontextmanager
    async def _composed(app):
        async with user_lifespan(app):
            async with _cleanup_ctx(app):
                yield

    return _composed


def create_admin(
    app=None,
    *,
    config=None,
    title: str | None = None,
    lifespan=None,
    **fastapi_kwargs,
):
    """Create and return a fully configured FastAPI admin app.

    **Factory mode** — preferred for new projects::

        app = create_admin(
            config=CoreAdminConfig.from_settings(settings),
            title="My Admin",
            lifespan=lifespan,
        )

    **Existing-app mode** — mount AdminFoundry onto an already-created app::

        existing_app = FastAPI(...)
        create_admin(existing_app, config=config)

    In both modes the full wiring is applied and the app is returned.
    ``config`` must always be passed as a keyword argument.
    """
    from fastapi import FastAPI
    from adminfoundry.core.config import CoreAdminConfig
    from adminfoundry.settings import settings as _settings

    config = config or CoreAdminConfig()

    global _admin_config
    _admin_config = config

    if app is None:
        effective_lifespan = _make_lifespan(
            lifespan,
            _settings.ENABLE_CLEANUP_TASK,
            _settings.CLEANUP_INTERVAL_SECONDS,
        )
        app = FastAPI(title=title or "adminfoundry", lifespan=effective_lifespan, **fastapi_kwargs)

    _setup_state(app, config)
    _install_exception_handlers(app)
    _install_middleware(app, config)
    _install_framework_defaults(config)
    _install_core_routers(app, config)
    _install_extensions(app, config)   # before CRUD so extension fixed-path routes win over /{model_name}
    _install_admin_crud(app, config)
    _install_admin_ui(app, config)
    _install_audit(app)

    return app


def _setup_state(app, config) -> None:
    from adminfoundry.settings import settings as _settings
    if config.database_url is not None:
        _settings.DATABASE_URL = config.database_url
        import adminfoundry.database as _db
        _db.configure(config.database_url, debug=_settings.DEBUG)
    else:
        import adminfoundry.database as _db
        _db._ensure_configured()

    if config.secret_key is not None:
        _settings.SECRET_KEY = config.secret_key

    from adminfoundry.auth_provider import AuthProvider
    provider = config.auth_provider or AuthProvider()
    if config.user_model is not None:
        from adminfoundry.models.protocols import validate_user_model
        validate_user_model(config.user_model)
        provider.user_model = config.user_model
    app.state.auth_provider = provider

    from adminfoundry import cache as _cache_mod, storage as _storage_mod, i18n as _i18n_mod
    if config.cache_backend:
        _cache_mod.configure(config.cache_backend)
    if config.storage_backend:
        _storage_mod.configure(config.storage_backend)
    if config.default_language:
        _i18n_mod.set_default_language(config.default_language)


def _install_exception_handlers(app) -> None:
    from fastapi.exceptions import RequestValidationError
    from adminfoundry.middleware.errors import validation_exception_handler
    app.add_exception_handler(RequestValidationError, validation_exception_handler)


def _install_framework_defaults(config) -> None:
    from adminfoundry.admin.default_admins import register_framework_defaults
    register_framework_defaults(enable_multi_tenant=config.enable_multi_tenant)


def _install_middleware(app, config) -> None:
    # FastAPI stacks middleware in reverse — first added is innermost (closest to handler).
    from adminfoundry.middleware.errors import UnhandledExceptionMiddleware
    from adminfoundry.middleware.security_headers import SecurityHeadersMiddleware
    from adminfoundry.middleware.rate_limit import RateLimitMiddleware
    from adminfoundry.middleware.logging import RequestLoggingMiddleware
    from adminfoundry.settings import settings as _settings

    app.add_middleware(UnhandledExceptionMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    if config.enable_multi_tenant:
        _settings.MULTI_TENANT = True
        _settings.TENANT_RESOLUTION_STRATEGY = config.tenant_resolution
        from adminfoundry.tenancy.middleware import TenantMiddleware
        app.add_middleware(TenantMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestLoggingMiddleware)


def _install_core_routers(app, config) -> None:
    from adminfoundry.routers import health, users, roles
    app.include_router(health.router)
    app.include_router(users.router)
    app.include_router(roles.router)
    if config.include_auth_routes:
        from adminfoundry.routers import auth
        app.include_router(auth.router)
    if config.enable_multi_tenant:
        from adminfoundry.routers import tenants
        app.include_router(tenants.router)


def _install_admin_crud(app, config) -> None:
    app.include_router(router)


def _install_extensions(app, config) -> None:
    from adminfoundry.admin.dashboard.registry import dashboard_registry
    from adminfoundry.admin.dashboard.builtins import DEFAULT_WIDGETS
    from adminfoundry.extensions import extension_registry

    # Reset singletons so create_admin() can be called multiple times (e.g., in tests)
    extension_registry._extensions.clear()

    if config.dashboard_widgets is not None and config.dashboard_widgets_mode == "replace":
        base = list(config.dashboard_widgets)
    elif config.dashboard_widgets is not None:
        base = list(DEFAULT_WIDGETS) + list(config.dashboard_widgets)
    else:
        base = list(DEFAULT_WIDGETS)
    dashboard_registry.reset(base=base)

    for ext in config.extensions:
        extension_registry.register(ext)
        ext.get_models()  # import side-effect registers extension tables with Base.metadata
        for ext_router in ext.get_routers():
            app.include_router(ext_router)
        for w in ext.get_dashboard_widgets():
            dashboard_registry.register(w)


def _install_admin_ui(app, config) -> None:
    from adminfoundry.routers import admin_ui as _admin_ui_module
    from adminfoundry.settings import settings as _settings
    _admin_ui_module._locale_defaults = {
        "language": config.default_language,
        "date_format": config.default_date_format,
        "date_pattern": config.default_date_pattern,
        "show_timezone": config.default_show_timezone,
    }
    _admin_ui_module._extra_i18n = config.extra_i18n

    if config.enable_builtin_ui:
        from adminfoundry.routers.admin_ui import router as admin_ui_router, get_static_app
        ui_path = _settings.ADMIN_UI_PATH
        app.mount(f"{ui_path}/static", get_static_app(), name="admin-static")
        app.include_router(admin_ui_router, prefix=ui_path)


def _install_audit(app) -> None:
    from adminfoundry.routers.audit import router as audit_router
    app.include_router(audit_router)
    # Audit middleware is always active — core infrastructure, not optional
    from adminfoundry.middleware.audit import AuditMiddleware
    app.add_middleware(AuditMiddleware)
