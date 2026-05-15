"""Admin dashboard and compatibility endpoints."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from adminfoundry.admin.contract import CONTRACT_VERSION
from adminfoundry.admin.ui_renderer import get_support_matrix
from adminfoundry.database import get_admin_db
from adminfoundry.dependencies import get_current_user
from adminfoundry.models.user import User
from adminfoundry.tenancy.resolver import resolve_impersonation_tenant as _resolve_impersonation_tenant

router = APIRouter()


@router.get("/dashboard")
async def admin_dashboard(
    request: Request,
    db: AsyncSession = Depends(get_admin_db),
    current_user: User = Depends(get_current_user),
):
    """Return rendered dashboard widgets for the current user."""
    from adminfoundry.admin.dashboard.registry import dashboard_registry

    payload = getattr(request.state, "token_payload", {})
    t = await _resolve_impersonation_tenant(payload, getattr(request.state, "tenant", None), db)
    if t is not None:
        request.state.tenant = t

    provider = getattr(request.app.state, "auth_provider", None)
    is_super = provider.is_superadmin(current_user) if provider else getattr(current_user, "is_superadmin", False)

    result = []
    for w in dashboard_registry.for_user(is_super):
        try:
            data = await w.get_data(current_user, db, request)
        except Exception:
            data = {}
        result.append({"id": w.id, "title": w.title, "type": w.widget_type(), "data": data})
    return {"widgets": result}


@router.get("/compatibility")
async def admin_compatibility(
    _: User = Depends(get_current_user),
):
    """Multi-surface compatibility manifest."""
    matrix = get_support_matrix()
    return {
        "contract_version": CONTRACT_VERSION,
        "surfaces": {
            "builtin_ui": {
                "renderer": matrix["renderer"],
                "version": matrix["version"],
                "supported_features": matrix["supported"],
            },
            "external_client": {
                "note": "Must consume the same admin contract endpoints as builtin UI.",
                "additional_hints": ["renderer_hints", "async_actions", "requires_approval"],
            },
            "api_only": {
                "note": "All contract endpoints remain functional when builtin UI is disabled.",
            },
        },
        "baseline_flows": [
            "list", "detail", "create", "update", "delete",
            "search", "filter", "order", "pagination",
            "tenant_context", "impersonation_indicator",
            "auth_login", "auth_logout", "auth_refresh",
        ],
        "advanced_flows": [
            "workflow_approval", "bulk_action",
            "import_export", "job_tracking", "step_up_auth",
            "session_management", "audit_visibility",
        ],
        "breaking_change_policy": (
            "Major contract_version increment signals a breaking change. "
            "Clients must refuse to operate against an unsupported major version."
        ),
    }
