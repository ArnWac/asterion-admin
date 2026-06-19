from __future__ import annotations

from asterion.admin.policy import ReadOnlyPolicy
from asterion.models.audit_log import AuditLog
from asterion.models.tenant_rbac import (
    TenantMembershipRole,
    TenantRole,
    TenantRolePermission,
)
from asterion.registry import ModelAdmin


class TenantRoleAdmin(ModelAdmin):
    model = TenantRole

    label = "Tenant Role"
    label_plural = "Tenant Roles"
    description = "Tenant-local roles used for permission assignment."

    list_display = ["name", "description", "is_system", "created_at"]
    search_fields = ["name", "description"]
    ordering = ["name"]
    readonly_fields = ["id", "created_at", "updated_at"]


class TenantRolePermissionAdmin(ModelAdmin):
    model = TenantRolePermission

    label = "Tenant Role Permission"
    label_plural = "Tenant Role Permissions"
    description = "Permission keys assigned to tenant-local roles."

    list_display = ["role_id", "permission_key", "created_at"]
    search_fields = ["permission_key"]
    ordering = ["permission_key"]
    readonly_fields = ["id", "created_at", "updated_at"]


class TenantMembershipRoleAdmin(ModelAdmin):
    model = TenantMembershipRole

    label = "Tenant Membership Role"
    label_plural = "Tenant Membership Roles"
    description = "Tenant-local mapping between a global TenantMembership and a tenant-local role."

    list_display = ["membership_id", "role_id", "created_at"]
    ordering = ["membership_id"]
    readonly_fields = ["id", "created_at", "updated_at"]


class AuditLogAdmin(ModelAdmin):
    """Read-only admin on the framework's :class:`AuditLog` table.

    Roadmap 5.1: gives every app an immediate Audit-UI without an
    extension — list / filter / search the audit trail, drill into one
    row to see the diff in ``changes``. Write paths are blocked by
    :class:`~asterion.admin.policy.ReadOnlyPolicy` so the data
    stays append-only at the admin layer too (the publisher is
    :mod:`asterion.audit.service`, not the admin).

    ``actor_label`` is denormalised into the row at write time, so
    list views show "alice@example.com" without an ORM hop. The
    ``changes`` blob is rendered by the generic JSON adapter in v1;
    a dedicated diff viewer comes in 5.1b.
    """

    model = AuditLog

    label = "Audit Log"
    label_plural = "Audit Logs"
    description = "Immutable record of admin write operations and login events."

    policy = ReadOnlyPolicy()

    list_display = [
        "created_at",
        "actor_label",
        "method",
        "path",
        "status_code",
        "resource",
        "record_id",
        "action",
    ]
    search_fields = [
        "actor_label",
        "resource",
        "record_id",
        "action",
        "path",
        "ip_address",
    ]
    filter_fields = [
        "method",
        "status_code",
        "resource",
        "action",
        "tenant_id",
    ]
    # Newest first is the obvious default — operators inspecting an
    # audit log almost always want the most recent activity on top.
    ordering = ["-created_at"]
    # Everything is server-managed — keep the detail view honest.
    readonly_fields = [
        "id",
        "created_at",
        "updated_at",
        "method",
        "path",
        "status_code",
        "actor_user_id",
        "tenant_id",
        "resource",
        "record_id",
        "action",
        "actor_label",
        "changes",
        "ip_address",
    ]


BUILTIN_TENANT_ADMINS = (
    TenantRoleAdmin,
    TenantRolePermissionAdmin,
    TenantMembershipRoleAdmin,
)

#: Read-only admins on framework-owned tables (Roadmap 5.1). Installed
#: by default alongside the tenant admins so any app with
#: ``enable_builtin_admins=True`` gets the Audit-UI for free.
BUILTIN_AUDIT_ADMINS = (AuditLogAdmin,)


__all__ = [
    "BUILTIN_AUDIT_ADMINS",
    "BUILTIN_TENANT_ADMINS",
    "AuditLogAdmin",
    "TenantMembershipRoleAdmin",
    "TenantRoleAdmin",
    "TenantRolePermissionAdmin",
]
