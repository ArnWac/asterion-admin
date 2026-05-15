"""Admin dashboard and compatibility endpoints."""
import asyncio
import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from adminfoundry.admin.contract import CONTRACT_VERSION
from adminfoundry.admin.dashboard.responses import DashboardResponse, DashboardWidgetResponse
from adminfoundry.admin.dashboard.widget import DashboardWidgetContext
from adminfoundry.admin.ui_renderer import get_support_matrix
from adminfoundry.database import get_admin_db
from adminfoundry.dependencies import get_current_user
from adminfoundry.models.user import User
from adminfoundry.tenancy.resolver import resolve_impersonation_tenant as _resolve_impersonation_tenant

_log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/dashboard", response_model=DashboardResponse)
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
    tenant = getattr(request.state, "tenant", None)

    provider = getattr(request.app.state, "auth_provider", None)
    is_super = provider.is_superadmin(current_user) if provider else getattr(current_user, "is_superadmin", False)

    ctx = DashboardWidgetContext(
        user=current_user,
        db=db,
        request=request,
        tenant=tenant,
        tenant_id=str(tenant.id) if tenant is not None else None,
        is_superadmin=is_super,
    )

    async def _fetch(w) -> DashboardWidgetResponse | None:
        if not await w.is_visible(ctx):
            return None
        try:
            data = await w.get_data(ctx)
            error = None
        except Exception:
            _log.exception("Dashboard widget %r failed", w.id)
            data = {}
            error = "widget_failed"
        return DashboardWidgetResponse(id=w.id, title=w.title, type=w.type, data=data, error=error)

    results = await asyncio.gather(*(_fetch(w) for w in dashboard_registry.all()))
    return DashboardResponse(widgets=[r for r in results if r is not None])


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
