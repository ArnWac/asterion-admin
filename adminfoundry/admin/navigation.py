"""Builds the navigation structure for the current user and context."""
from __future__ import annotations
from adminfoundry.admin.registry import Registry
from adminfoundry.schemas.navigation import NavItem, NavigationResponse


def build_navigation(user, token_payload: dict, registry: Registry, tenant=None) -> NavigationResponse:
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
                continue  # hide global models in tenant context
            if tenant is None and admin.tenant_scoped:
                continue  # hide tenant-scoped models in root panel
            items.append(NavItem(
                model=admin.model_name,
                label=admin.display_label,
                label_plural=admin.display_label_plural,
                url=f"/api/v1/admin/{admin.model_name}",
                tenant_scoped=admin.tenant_scoped,
            ))
    elif user.is_superadmin and is_impersonating and tenant is not None:
        # Self-impersonation to enter a tenant panel: show tenant-scoped models
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
        is_tenant_admin = any(
            r.name == "tenant_admin" and r.tenant_id == tenant.id
            for r in (user.roles or [])
        )
        user_role_names = {r.name for r in (user.roles or [])}
        for admin in registry.all():
            if not admin.tenant_scoped:
                continue
            if is_tenant_admin:
                # Tenant admin sees all tenant-scoped models
                pass
            else:
                # Regular user: only models explicitly opened via access_roles
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
