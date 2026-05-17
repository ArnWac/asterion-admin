"""Shared helper functions used across admin route modules."""
from __future__ import annotations

from fastapi import HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from adminfoundry.admin.registry import admin_site


def _get_runtime(request: Request):
    return getattr(getattr(request.app, "state", None), "adminfoundry", None)


def _get_multi_tenant_flag(request: Request) -> bool:
    """Read enable_multi_tenant from per-app runtime; fall back to settings for legacy apps."""
    runtime = _get_runtime(request)
    if runtime is not None:
        return runtime.config.enable_multi_tenant
    from adminfoundry.settings import settings
    return settings.MULTI_TENANT


async def _enforce_method_caps(
    model_admin, user, token_payload: dict, method: str, db: AsyncSession, tenant_auth=None, membership=None
) -> None:
    """Enforce per-HTTP-method RolePermission DB check for non-superadmin users.

    Skips superadmins entirely (their access is governed by _check_model_access).
    Only fires when DB records exist for the user+model combination — falls through
    gracefully when no RolePermission rows are configured.
    """
    if user.is_superadmin:
        return
    from adminfoundry.authz.role_caps import fetch_model_caps
    caps = await fetch_model_caps(user, model_admin.model_name, db, tenant_auth=tenant_auth, membership=membership)
    if caps is None:
        return  # no DB rows → ModelAdmin config already checked by _check_model_access
    cap_key = {
        "list": "can_list",
        "create": "can_create",
        "read": "can_read",
        "update": "can_update",
        "delete": "can_delete",
    }.get(method)
    if cap_key and not caps.get(cap_key, True):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")


def _model_supports_soft_delete(model_admin) -> bool:
    return getattr(model_admin, "soft_delete", False) and hasattr(model_admin.model, "deleted_at")


def _get_admin_or_404(model_name: str):
    model_admin = admin_site.get(model_name)
    if model_admin is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model '{model_name}' not registered",
        )
    return model_admin


def _check_model_access(
    model_admin,
    user,
    token_payload: dict,
    tenant=None,
    multi_tenant: bool = False,
    tenant_auth=None,
    membership=None,
) -> None:
    """Raise 403 if the user lacks access to this model's admin CRUD interface.

    Superadmin without impersonation token in a tenant context → 403 (must use impersonation).
    Superadmin with impersonation token in tenant context → only tenant-scoped models allowed.
    """
    is_impersonating = bool(token_payload.get("impersonated_by"))

    if user.is_superadmin and not is_impersonating:
        if multi_tenant and tenant is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Use an impersonation token to access tenant panels",
            )
        if multi_tenant and tenant is None and model_admin.tenant_scoped:
            # global_only_in_root_panel models are accessible from the root panel;
            # _tenant_filter applies WHERE tenant_id IS NULL to scope to global records.
            if not getattr(model_admin, "global_only_in_root_panel", False):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Tenant context required — use impersonation to access tenant-scoped models",
                )
        return

    if user.is_superadmin and is_impersonating:
        token_tenant_id = token_payload.get("tenant_id")
        # Subdomain mode: token must match the resolved tenant
        if tenant is not None and token_tenant_id != str(tenant.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Impersonation token is not valid for this tenant",
            )
        # Same-origin mode: no subdomain, but token must carry tenant_id
        if tenant is None and not token_tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Impersonation token is not valid for this tenant",
            )
        if model_admin.tenant_scoped:
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only tenant-scoped models are accessible during tenant impersonation",
        )

    if tenant is not None and model_admin.tenant_scoped:
        if tenant_auth is not None:
            if tenant_auth.has_role("tenant_admin"):
                return
        elif membership is not None:
            for r in membership.roles:
                if r.name == "tenant_admin" and str(r.tenant_id) == str(tenant.id):
                    return

    if getattr(model_admin, "admin_only", True):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Superadmin required")
    access_roles = getattr(model_admin, "access_roles", [])
    if access_roles:
        if tenant_auth is not None:
            effective_roles = tenant_auth.role_names()
        elif membership is not None:
            effective_roles = {r.name for r in (membership.roles or [])}
        else:
            effective_roles = {r.name for r in (user.roles or [])}
        if not effective_roles.intersection(access_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
            )


def _require_superadmin_or_impersonating(user, token_payload: dict, request: Request, tenant_auth=None, membership=None) -> None:
    """Allow superadmin (root panel or impersonating) OR tenant admin in their own tenant."""
    runtime = _get_runtime(request)
    if runtime is not None:
        provider = runtime.auth_provider
    else:
        from adminfoundry.auth_provider import AuthProvider
        provider = AuthProvider()
    if provider.is_superadmin(user):
        return
    tenant = getattr(request.state, "tenant", None)
    if tenant is not None:
        if tenant_auth is not None:
            if tenant_auth.has_role("tenant_admin"):
                return
        elif membership is not None:
            tenant_id_str = str(tenant.id)
            for r in membership.roles:
                if r.name == "tenant_admin" and str(r.tenant_id) == tenant_id_str:
                    return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Superadmin required")


def _tenant_filter(request: Request, model_admin):
    """Return a SQLAlchemy filter for tenant-scoped models, or None.

    Subdomain mode              → WHERE tenant_id = <tenant.id>
    Same-origin impersonation   → WHERE tenant_id = <token.tenant_id>
    Root panel, global model    → WHERE tenant_id IS NULL
    Root panel, non-scoped      → no filter (superadmin sees all)
    """
    if not model_admin.tenant_scoped or not _get_multi_tenant_flag(request):
        return None
    if not hasattr(model_admin.model, "tenant_id"):
        return None
    tenant = getattr(request.state, "tenant", None)
    if tenant is not None:
        return model_admin.model.tenant_id == str(tenant.id)
    token_payload = getattr(request.state, "token_payload", {})
    if token_payload.get("impersonated_by") and token_payload.get("tenant_id"):
        return model_admin.model.tenant_id == token_payload["tenant_id"]
    if getattr(model_admin, "global_only_in_root_panel", False):
        return model_admin.model.tenant_id.is_(None)
    return None


def _validate_body(schema_class, body: dict):
    """Validate raw body dict against a dynamic schema; raise 422 on failure."""
    try:
        return schema_class.model_validate(body)
    except ValidationError as exc:
        from adminfoundry.middleware.errors import _serializable_errors
        raise HTTPException(
            status_code=422,
            detail={"detail": "Validation error", "errors": _serializable_errors(exc.errors())},
        )
