"""Builds the navigation structure for the current user and context."""
from __future__ import annotations
from adminfoundry.admin.registry import Registry
from adminfoundry.schemas.navigation import NavItem, NavigationResponse


def build_navigation(user, token_payload: dict, registry: Registry) -> NavigationResponse:
    """Return visible navigation items.

    Impersonation tokens and non-superadmins see an empty nav — they have no
    admin access.
    """
    is_impersonating = bool(token_payload.get("impersonated_by"))
    items: list[NavItem] = []

    if user.is_superadmin and not is_impersonating:
        for admin in registry.all():
            items.append(NavItem(
                model=admin.model_name,
                label=admin.display_label,
                label_plural=admin.display_label_plural,
                url=f"/api/v1/admin/{admin.model_name}",
                tenant_scoped=admin.tenant_scoped,
            ))

    return NavigationResponse(items=items)
