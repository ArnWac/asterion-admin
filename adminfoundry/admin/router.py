"""Admin router aggregator — assembles sub-route modules.

Route registration order matters: fixed-path routes (contract, dashboard, profile,
preferences, permissions) must be included before the parameterized CRUD catch-all routes.

App factory and installer logic moved to `adminfoundry.core.app_factory` and
`adminfoundry.core.installers` in the V1 refactor.
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from adminfoundry.admin.registry import admin_site as _admin_site
from adminfoundry.dependencies import get_current_user as _get_current_user
from adminfoundry.models.user import User as _User
from adminfoundry.tenancy.dependencies import require_tenant_membership as _require_tenant_membership

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("")
async def list_registered_models(
    request: Request,
    current_user: _User = Depends(_get_current_user),
    _membership=Depends(_require_tenant_membership),
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


__all__ = ["router"]
