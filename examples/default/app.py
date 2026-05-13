import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from adminfoundry.settings import settings
from adminfoundry.core.config import CoreAdminConfig
from adminfoundry.routers import auth, health, users, roles, tenants
from adminfoundry.middleware.errors import validation_exception_handler, UnhandledExceptionMiddleware
from adminfoundry.middleware.logging import RequestLoggingMiddleware, configure_json_logging
from adminfoundry.middleware.tenant import TenantMiddleware
from adminfoundry.middleware.security_headers import SecurityHeadersMiddleware
from adminfoundry.middleware.rate_limit import RateLimitMiddleware
from adminfoundry.admin.router import create_admin
from adminfoundry.auth_provider import AuthProvider
from adminfoundry.cleanup import periodic_cleanup
import examples.default.admin_config  # noqa: F401 — trigger admin registrations

config = CoreAdminConfig.from_settings(settings)

if settings.LOG_JSON:
    configure_json_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(periodic_cleanup())
    try:
        yield
    finally:
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass


app = FastAPI(title="Adminfoundry Admin", lifespan=lifespan)
app.state.auth_provider = config.auth_provider or AuthProvider()

app.add_middleware(UnhandledExceptionMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(TenantMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(RequestValidationError, validation_exception_handler)

if config.include_auth_routes:
    app.include_router(auth.router)
app.include_router(health.router)
app.include_router(users.router)
app.include_router(roles.router)
app.include_router(tenants.router)

# Mount user-provided extension routers in registration order
from adminfoundry.extensions import extension_registry
for _ext in config.extensions:
    extension_registry.register(_ext)
    for _ext_router in _ext.get_routers():
        app.include_router(_ext_router)

create_admin(app, config=config)

if config.enable_builtin_ui:
    from adminfoundry.routers.admin_ui import router as admin_ui_router, get_static_app
    app.mount(
        f"{settings.ADMIN_UI_PATH}/static",
        get_static_app(),
        name="admin-static",
    )
    app.include_router(admin_ui_router, prefix=settings.ADMIN_UI_PATH)
