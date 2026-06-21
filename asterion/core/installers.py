from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from asterion.actions.router import build_actions_router
from asterion.admin.login_contract_router import router as login_contract_router
from asterion.admin.member_router import router as member_router
from asterion.admin.navigation_router import router as navigation_router
from asterion.admin.permission_matrix_router import (
    router as permission_matrix_router,
)
from asterion.admin.saved_filter_router import router as saved_filter_router
from asterion.auth.router import router as auth_router
from asterion.auth.two_factor_router import router as two_factor_router
from asterion.contract.router import router as contract_router
from asterion.core.config import CoreAdminConfig
from asterion.core.health import router as health_router
from asterion.core.middleware import (
    AccessLogMiddleware,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
)
from asterion.crud.router import build_crud_router
from asterion.root.router import router as root_router
from asterion.storage.router import router as storage_router


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
        from asterion.tenancy.middleware import TenantMiddleware

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
        if config.content_security_policy and config.enable_builtin_ui:
            logging.getLogger("asterion").warning(
                "content_security_policy is set while the bundled admin UI is "
                "enabled. The bundled UI relies on inline <script> config blocks "
                "that a strict 'script-src' will block (the UI then fails to "
                "boot). Ensure the policy permits them (e.g. a nonce/'unsafe-inline') "
                "or set enable_builtin_ui=False for API-first deployments."
            )
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
    # sibling of /_contract — see asterion/admin/navigation_router.py.
    app.include_router(
        navigation_router,
        prefix=config.admin_api_prefix,
        tags=["admin-navigation"],
    )

    # Anonymous-readable login-page metadata (OAuth provider buttons).
    # Mounted on the admin prefix so the path layout stays consistent;
    # the endpoint itself does not require auth. See
    # asterion/admin/login_contract_router.py for why this is a
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

        from asterion.ui import STATIC_DIR
        from asterion.ui import router as ui_router

        # Mount static BEFORE including the UI router so requests to
        # /<ui_path>/static/* are not swallowed by the /{resource} catch-all.
        app.mount(
            f"{config.admin_ui_path}/static",
            StaticFiles(directory=str(STATIC_DIR / "admin")),
            name="asterion-static",
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

    # Tenant member-management. Same static-prefix-before-dynamic-CRUD
    # constraint — ``_members`` must beat ``/{resource}``.
    app.include_router(
        member_router,
        prefix=config.admin_api_prefix,
        tags=["admin-members"],
    )

    # CRUD + action routes are registered EXPLICITLY per registered resource
    # (``/employees``, ``/employees/_actions/{action}``, …) rather than via a
    # greedy ``/{resource}`` catch-all. The registry is already frozen at this
    # point (see core/app_factory.py), so every resource name is known. Effect:
    # a path under the admin prefix that is NOT a registered resource matches no
    # CRUD/action route, so an embedding app can claim it with a plain
    # ``app.include_router`` after ``create_admin`` — no AdminExtension or
    # route-ordering tricks. ``register_routes`` on extensions still works.
    resources = app.state.asterion.registry.model_names()

    # Actions before CRUD so /{resource}/_actions/{action} is matched first.
    app.include_router(
        build_actions_router(resources),
        prefix=config.admin_api_prefix,
        tags=["admin-actions"],
    )

    app.include_router(
        build_crud_router(resources),
        prefix=config.admin_api_prefix,
        tags=["admin-crud"],
    )
