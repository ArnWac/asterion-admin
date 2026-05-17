"""Admin contract endpoints — registry overview, context, navigation, capabilities, client-config.

These fixed-path routes must be included BEFORE /{model_name} CRUD routes so FastAPI
doesn't match literal path segments (e.g. "context") as a model_name parameter.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from adminfoundry.admin.capabilities import build_capabilities, build_admin_context
from adminfoundry.admin.contract import build_model_contract, CONTRACT_VERSION
from adminfoundry.admin.navigation import build_navigation
from adminfoundry.admin.registry import admin_site
from adminfoundry.admin.ui_renderer import get_support_matrix
from adminfoundry.database import get_db
from adminfoundry.dependencies import get_current_user
from adminfoundry.models.user import User
from adminfoundry.schemas.client_config import ClientConfigResponse
from adminfoundry.tenancy.dependencies import require_tenant_membership
from adminfoundry.tenancy.resolver import resolve_impersonation_tenant as _resolve_impersonation_tenant

router = APIRouter()


@router.get("/context")
async def admin_context(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return authenticated admin context — user info, tenant, impersonation state."""
    payload = getattr(request.state, "token_payload", {})
    t = await _resolve_impersonation_tenant(payload, getattr(request.state, "tenant", None), db)
    if t is not None:
        request.state.tenant = t
    return build_admin_context(current_user, payload, request)


@router.get("/navigation")
async def admin_navigation(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _membership=Depends(require_tenant_membership),
):
    """Return visible navigation structure for the current user and context."""
    payload = getattr(request.state, "token_payload", {})
    tenant = await _resolve_impersonation_tenant(
        payload, getattr(request.state, "tenant", None), db
    )
    membership = getattr(request.state, "tenant_membership", None)
    return build_navigation(current_user, payload, admin_site, tenant=tenant, membership=membership)


@router.get("/capabilities")
async def admin_capabilities(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _membership=Depends(require_tenant_membership),
):
    """Return UI-safe capability metadata for the current user and context."""
    payload = getattr(request.state, "token_payload", {})
    from adminfoundry.authz.role_caps import fetch_all_model_caps
    membership = getattr(request.state, "tenant_membership", None)
    all_db_caps = await fetch_all_model_caps(current_user, db, membership=membership)
    in_tenant_context = bool(
        payload.get("impersonated_by") or getattr(request.state, "tenant", None)
    )
    return build_capabilities(
        current_user, payload, admin_site, all_db_caps or None,
        in_tenant_context=in_tenant_context,
    )


@router.get("/client-config", response_model=ClientConfigResponse)
async def client_config(
    _: User = Depends(get_current_user),
):
    """Bootstrap config for external renderer clients."""
    matrix = get_support_matrix()
    return ClientConfigResponse(
        contract_version=CONTRACT_VERSION,
        renderer_id=matrix["renderer"],
        renderer_version=matrix["version"],
        supported_features=matrix["supported"],
        endpoints={
            "context": "/api/v1/admin/context",
            "navigation": "/api/v1/admin/navigation",
            "capabilities": "/api/v1/admin/capabilities",
            "registry": "/api/v1/admin",
            "client_config": "/api/v1/admin/client-config",
            "model_meta": "/api/v1/admin/{model}/meta",
            "model_list": "/api/v1/admin/{model}",
            "model_lookup": "/api/v1/admin/{model}/lookup",
        },
        breaking_change_policy=(
            "A breaking change increments the major contract_version. "
            "Clients must check contract_version on bootstrap and refuse "
            "to operate against an unsupported major version."
        ),
        additive_change_policy=(
            "New optional fields may be added without changing contract_version. "
            "Clients must ignore unknown fields (Postel's law)."
        ),
    )
