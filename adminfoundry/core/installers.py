from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from adminfoundry.actions.router import router as actions_router
from adminfoundry.admin.login_contract_router import router as login_contract_router
from adminfoundry.admin.navigation_router import router as navigation_router
from adminfoundry.admin.permission_matrix_router import (
    router as permission_matrix_router,
)
from adminfoundry.admin.saved_filter_router import router as saved_filter_router
from adminfoundry.auth.router import router as auth_router
from adminfoundry.auth.two_factor_router import router as two_factor_router
from adminfoundry.contract.router import router as contract_router
from adminfoundry.core.config import CoreAdminConfig
from adminfoundry.core.health import router as health_router
from adminfoundry.core.middleware import (
    AccessLogMiddleware,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
)
from adminfoundry.crud.router import router as crud_router
from adminfoundry.root.router import router as root_router
from adminfoundry.storage.router import router as storage_router


def install_middleware(
    app: FastAPI,
    config: CoreAdminConfig,
) -> None:
    """Install middlewares in OUTER→INNER order.

    Starlette runs middlewares as a stack: the LAST one added runs FIRST
    on the request path. We want request_id available everywhere, so it
    must be the outermost middleware (added last).
    """
    # Inner-most: tenant resolution (depends on request body / headers).
    if config.enable_multi_tenant:
        from adminfoundry.tenancy.middleware import TenantMiddleware

        app.add_middleware(TenantMiddleware)

    # CORS only if origins are configured. Validate already rejected the
    # unsafe ``["*"]`` + credentials=True combination.
    if config.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(config.cors_origins),
            allow_credentials=config.cors_allow_credentials,
            allow_methods=list(config.cors_allow_methods),
            allow_headers=list(config.cors_allow_headers),
        )

    if config.security_headers_enabled:
        app.add_middleware(SecurityHeadersMiddleware, csp=config.content_security_policy)

    # Access log sits just inside RequestIDMiddleware so its log records
    # already see request.state.request_id, but it still wraps everything
    # else (security headers, CORS, tenant resolution, handler) for
    # accurate duration measurement.
    app.add_middleware(AccessLogMiddleware)

    # Outer-most: request ID. Runs first on the request path, so
    # downstream middlewares + handlers can read request.state.request_id.
    app.add_middleware(RequestIDMiddleware)


def install_routes(
    app: FastAPI,
    config: CoreAdminConfig,
) -> None:
    # Liveness + readiness probes — no auth, no prefix, no tags so they
    # don't pollute the OpenAPI schema.
    app.include_router(health_router)

    app.include_router(
        auth_router,
        prefix=config.auth_api_prefix,
        tags=["auth"],
    )

    app.include_router(
        two_factor_router,
        prefix=config.auth_api_prefix,
        tags=["auth-2fa"],
    )

    app.include_router(
        contract_router,
        prefix=config.admin_api_prefix,
        tags=["admin-contract"],
    )

    # Per-user navigation (extension-contributed items, permission-filtered).
    # Mounted on the same admin prefix so the UI can hit /_navigation as a
    # sibling of /_contract — see adminfoundry/admin/navigation_router.py.
    app.include_router(
        navigation_router,
        prefix=config.admin_api_prefix,
        tags=["admin-navigation"],
    )

    # Anonymous-readable login-page metadata (OAuth provider buttons).
    # Mounted on the admin prefix so the path layout stays consistent;
    # the endpoint itself does not require auth. See
    # adminfoundry/admin/login_contract_router.py for why this is a
    # narrow public surface rather than widening /_contract.
    app.include_router(
        login_contract_router,
        prefix=config.admin_api_prefix,
        tags=["admin-login-contract"],
    )

    # Superadmin-only root routes — never use tenant middleware /
    # TenantAuthContext; require_superadmin rejects impersonation tokens.
    app.include_router(
        root_router,
        prefix=config.root_api_prefix,
        tags=["root"],
    )

    if config.enable_builtin_ui:
        from fastapi.staticfiles import StaticFiles

        from adminfoundry.ui import STATIC_DIR
        from adminfoundry.ui import router as ui_router

        # Mount static BEFORE including the UI router so requests to
        # /<ui_path>/static/* are not swallowed by the /{resource} catch-all.
        app.mount(
            f"{config.admin_ui_path}/static",
            StaticFiles(directory=str(STATIC_DIR / "admin")),
            name="adminfoundry-static",
        )
        app.include_router(
            ui_router,
            prefix=config.admin_ui_path,
            tags=["admin-ui"],
        )

    # Saved filters live under /api/v1/admin/_saved_filters — must be
    # mounted before the dynamic /{resource} CRUD routes so the path
    # is matched as a static prefix.
    app.include_router(
        saved_filter_router,
        prefix=config.admin_api_prefix,
        tags=["admin-saved-filters"],
    )

    # Storage upload + serve (P4.4). Mounted before CRUD for the same
    # reason as saved_filter_router — the leading ``_storage`` segment
    # must be matched as a static prefix, not consumed by ``/{resource}``.
    # The router itself returns 503 when no backend is wired, so it's
    # always safe to mount.
    app.include_router(
        storage_router,
        prefix=config.admin_api_prefix,
        tags=["admin-storage"],
    )

    # Permission-matrix bulk read + bulk write (P5.2). Same routing
    # constraint as above — ``_permission_matrix`` must beat the
    # dynamic ``/{resource}`` CRUD routes.
    app.include_router(
        permission_matrix_router,
        prefix=config.admin_api_prefix,
        tags=["admin-permission-matrix"],
    )

    # Actions before CRUD so /{resource}/_actions/{action} is matched first.
    app.include_router(
        actions_router,
        prefix=config.admin_api_prefix,
        tags=["admin-actions"],
    )

    # Dynamic CRUD routes last.
    app.include_router(
        crud_router,
        prefix=config.admin_api_prefix,
        tags=["admin-crud"],
    )
