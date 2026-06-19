from __future__ import annotations

from asterion.builtins.admin import BUILTIN_AUDIT_ADMINS, BUILTIN_TENANT_ADMINS
from asterion.registry import AdminRegistry, ModelAdmin


def install_builtin_admins(
    registry: AdminRegistry,
    *,
    include_tenant_admins: bool = True,
    include_audit_admins: bool = True,
    extra_admins: tuple[type[ModelAdmin], ...] = (),
) -> None:
    """Register the framework's own admins on ``registry``.

    Tenant admins (``TenantRole`` / ``TenantRolePermission`` /
    ``TenantMembershipRole``) cover the RBAC surface; audit admins
    (``AuditLog``) give every app a read-only Audit-UI (Roadmap 5.1).
    Both flags are on by default — flip them off only when the host
    app is replacing the framework's models with its own.
    """
    if include_tenant_admins:
        for admin_class in BUILTIN_TENANT_ADMINS:
            if not registry.is_registered(admin_class.model):
                registry.register(admin_class)

    if include_audit_admins:
        for admin_class in BUILTIN_AUDIT_ADMINS:
            if not registry.is_registered(admin_class.model):
                registry.register(admin_class)

    for admin_class in extra_admins:
        if not registry.is_registered(admin_class.model):
            registry.register(admin_class)


__all__ = [
    "install_builtin_admins",
]
