from __future__ import annotations

from typing import Any

from sqlalchemy import select

from asterion.admin.policy import ReadOnlyPolicy
from asterion.models.audit_log import AuditLog
from asterion.models.tenant_audit_log import TenantAuditLog
from asterion.models.tenant_membership import TenantMembership
from asterion.models.tenant_rbac import (
    TenantMembershipRole,
    TenantRole,
    TenantRolePermission,
)
from asterion.models.user import User
from asterion.registry import ModelAdmin


async def _role_name_labels(session: Any, role_ids: set) -> dict[str, str]:
    """Batched ``role_id`` → role name map for the given ids (one query)."""
    role_ids = {r for r in role_ids if r is not None}
    if not role_ids:
        return {}
    rows = (
        await session.execute(
            select(TenantRole.id, TenantRole.name).where(TenantRole.id.in_(role_ids))
        )
    ).all()
    return {str(rid): name for rid, name in rows}


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
    # Managed through the per-role permission picker (the Tenant Role detail's
    # "Edit permissions"), so it stays routable but off the sidebar.
    show_in_nav = False

    async def resolve_list_labels(self, objs, *, session, ctx=None):
        return {"role_id": await _role_name_labels(session, {o.role_id for o in objs})}


class TenantMembershipRoleAdmin(ModelAdmin):
    model = TenantMembershipRole

    label = "Tenant Membership Role"
    label_plural = "Tenant Membership Roles"
    description = "Tenant-local mapping between a global TenantMembership and a tenant-local role."

    list_display = ["membership_id", "role_id", "created_at"]
    ordering = ["membership_id"]
    readonly_fields = ["id", "created_at", "updated_at"]
    # membership_id has no DB-level foreign key (cross-schema → public
    # tenant_memberships), so force the FK-picker widget; resolve_fk_options
    # below supplies its member-email options. role_id has a real FK and is
    # picked up generically.
    widgets = {"membership_id": "foreign_key"}

    async def resolve_fk_options(self, field, *, session, ctx=None, q=None, limit=100):
        """Member-email options for the cross-schema ``membership_id`` picker.

        Resolves membership ids to ``member email`` via a join into public
        (``TenantMembership`` → ``User``) through the request session, the same
        cross-schema path :meth:`resolve_list_labels` uses. ``role_id`` returns
        ``None`` so the generic resolver lists ``tenant_roles`` by name.
        """
        if field != "membership_id":
            return None
        stmt = select(TenantMembership.id, User.email).join(
            User, User.id == TenantMembership.user_id
        )
        if q and q.strip():
            stmt = stmt.where(User.email.ilike(f"%{q.strip()}%"))
        stmt = stmt.order_by(User.email.asc()).limit(limit)
        rows = (await session.execute(stmt)).all()
        return [{"value": str(mid), "label": email} for mid, email in rows]

    async def resolve_list_labels(self, objs, *, session, ctx=None):
        labels: dict[str, dict[str, str]] = {
            "role_id": await _role_name_labels(session, {o.role_id for o in objs}),
        }
        # membership_id points at a public TenantMembership (cross-schema, no
        # FK); resolve it to the member's email via the request session, whose
        # search_path includes public. One batched query.
        membership_ids = {o.membership_id for o in objs if o.membership_id is not None}
        if membership_ids:
            rows = (
                await session.execute(
                    select(TenantMembership.id, User.email)
                    .join(User, User.id == TenantMembership.user_id)
                    .where(TenantMembership.id.in_(membership_ids))
                )
            ).all()
            labels["membership_id"] = {str(mid): email for mid, email in rows}
        return labels


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


class TenantAuditLogAdmin(ModelAdmin):
    """Read-only admin on the per-tenant :class:`TenantAuditLog` table.

    Lives inside each tenant schema, so it surfaces only the calling
    tenant's own audit trail — physical isolation via ``search_path``,
    no cross-tenant filtering. Tenant-context admin events (CRUD /
    actions / import-export) are routed here; global events stay in the
    public :class:`AuditLogAdmin`. Append-only via
    :class:`~asterion.admin.policy.ReadOnlyPolicy`.

    It carries no ``tenant_id`` column (the schema is the tenant), so the
    list/detail are slightly leaner than the global audit admin.
    """

    model = TenantAuditLog

    label = "Audit Log"
    label_plural = "Audit Logs"
    description = "Immutable record of this tenant's admin write operations."

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
    ]
    ordering = ["-created_at"]
    readonly_fields = [
        "id",
        "created_at",
        "updated_at",
        "method",
        "path",
        "status_code",
        "actor_user_id",
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
    TenantAuditLogAdmin,
)

#: Read-only admins on framework-owned tables (Roadmap 5.1). Installed
#: by default alongside the tenant admins so any app with
#: ``enable_builtin_admins=True`` gets the Audit-UI for free.
BUILTIN_AUDIT_ADMINS = (AuditLogAdmin,)


__all__ = [
    "BUILTIN_AUDIT_ADMINS",
    "BUILTIN_TENANT_ADMINS",
    "AuditLogAdmin",
    "TenantAuditLogAdmin",
    "TenantMembershipRoleAdmin",
    "TenantRoleAdmin",
    "TenantRolePermissionAdmin",
]
