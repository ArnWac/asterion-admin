"""Builds the navigation structure for the current user and context."""
from __future__ import annotations
from adminfoundry.admin.registry import Registry
from adminfoundry.schemas.navigation import NavItem, NavigationResponse


def build_navigation(user, token_payload: dict, registry: Registry, tenant=None, tenant_auth=None, membership=None) -> NavigationResponse:
    """Return visible navigation items.

    Superadmin root panel (no tenant, not impersonating): all models visible.
    Superadmin impersonating in tenant context: tenant_scoped models only.
    Superadmin in tenant context without impersonation: blocked at API level — empty nav.
    Non-superadmin / other impersonation: empty nav.
    """
    is_impersonating = bool(token_payload.get("impersonated_by"))
    items: list[NavItem] = []

    if user.is_superadmin and not is_impersonating:
        for admin in registry.all():
            if tenant is not None and not admin.tenant_scoped:
                continue
            if tenant is None and admin.tenant_scoped and not admin.global_only_in_root_panel:
                continue
            items.append(NavItem(
                model=admin.model_name,
                label=admin.display_label,
                label_plural=admin.display_label_plural,
                url=f"/api/v1/admin/{admin.model_name}",
                tenant_scoped=admin.tenant_scoped,
            ))
    elif user.is_superadmin and is_impersonating and tenant is not None:
        for admin in registry.all():
            if admin.tenant_scoped:
                items.append(NavItem(
                    model=admin.model_name,
                    label=admin.display_label,
                    label_plural=admin.display_label_plural,
                    url=f"/api/v1/admin/{admin.model_name}",
                    tenant_scoped=True,
                ))
    elif not user.is_superadmin and tenant is not None:
        if tenant_auth is not None:
            is_tenant_admin = tenant_auth.has_role("tenant_admin")
            user_role_names = tenant_auth.role_names()
        else:
            effective_roles = list(membership.roles) if membership is not None else []
            is_tenant_admin = any(
                r.name == "tenant_admin" and str(r.tenant_id) == str(tenant.id)
                for r in effective_roles
            )
            user_role_names = {r.name for r in effective_roles}
        for admin in registry.all():
            if not admin.tenant_scoped:
                continue
            if not is_tenant_admin:
                if getattr(admin, "admin_only", True):
                    continue
                access_roles = getattr(admin, "access_roles", [])
                if access_roles and not user_role_names.intersection(access_roles):
                    continue
            items.append(NavItem(
                model=admin.model_name,
                label=admin.display_label,
                label_plural=admin.display_label_plural,
                url=f"/api/v1/admin/{admin.model_name}",
                tenant_scoped=True,
            ))

    return NavigationResponse(items=items)
