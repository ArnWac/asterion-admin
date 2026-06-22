"""Root admin router — superadmin-only endpoints.

These routes never go through tenant middleware / TenantAuthContext, and
``require_superadmin`` rejects impersonation tokens, so root operations
cannot be performed by an impersonated session.

The impersonation sub-router is mounted only when
``CoreAdminConfig.enable_impersonation`` is True (the default); users and
tenants are always mounted — the tenant list, in particular, powers the
superadmin tenant switcher in the UI.
"""

from __future__ import annotations

from fastapi import APIRouter

from asterion.root.impersonation import router as _impersonation_router
from asterion.root.tenants import router as _tenants_router
from asterion.root.users import router as _users_router


def build_root_router(*, enable_impersonation: bool = True) -> APIRouter:
    """Compose the root router, gating impersonation behind the config flag."""
    router = APIRouter()
    if enable_impersonation:
        router.include_router(_impersonation_router)
    router.include_router(_users_router)
    router.include_router(_tenants_router)
    return router


#: Default composition (impersonation included) for importers that want the
#: full root surface without a config. ``create_admin`` builds its own via
#: :func:`build_root_router` using the resolved ``enable_impersonation``.
router = build_root_router(enable_impersonation=True)
