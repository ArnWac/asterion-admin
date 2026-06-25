from __future__ import annotations

from typing import Any

from sqlalchemy import select

from asterion.admin.inline import InlineAdmin
from asterion.admin.policy import NoCreateDeletePolicy, ReadOnlyPolicy
from asterion.models.audit_log import AuditLog
from asterion.models.impersonation_log import ImpersonationLog
from asterion.models.tenant import Tenant
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


async def _email_labels(session: Any, user_ids: set) -> dict[str, str]:
    """Batched ``user_id`` → email map for the given ids (one query)."""
    ids = {u for u in user_ids if u is not None}
    if not ids:
        return {}
    rows = (await session.execute(select(User.id, User.email).where(User.id.in_(ids)))).all()
    return {str(uid): email for uid, email in rows}


async def _tenant_slug_labels(session: Any, tenant_ids: set) -> dict[str, str]:
    """Batched ``tenant_id`` → slug map for the given ids (one query)."""
    ids = {t for t in tenant_ids if t is not None}
    if not ids:
        return {}
    rows = (await session.execute(select(Tenant.id, Tenant.slug).where(Tenant.id.in_(ids)))).all()
    return {str(tid): slug for tid, slug in rows}


def _membership_email_stmt():
    """Base cross-schema select: ``TenantMembership.id`` → member email.

    The single join (membership → public ``User``) shared by the membership_id
    label resolver and FK-options picker; each call site adds its own
    ``WHERE`` / ordering so the join definition lives in one place.
    """
    return select(TenantMembership.id, User.email).join(
        User, User.id == TenantMembership.user_id
    )


class TenantRolePermissionInline(InlineAdmin):
    """Permission keys for a role, edited inline on the role detail.

    Replaces the standalone per-role permission picker: one ``Edit`` on
    the role now writes role fields + permission rows + member rows in a
    single transaction (the parent admin's inline write path)."""

    model = TenantRolePermission
    fk_name = "role_id"
    label = "Permissions"
    fields = ["permission_key"]
    ordering = ["permission_key"]
    extra = 1
    can_delete = True
    # Theme F: assign permission keys with a Django-style transfer widget
    # (available | assigned) instead of an add-row table — the universe of
    # keys comes from the public PermissionCatalog.
    widget = "dual_list"
    value_field = "permission_key"

    async def resolve_options(self, *, session, ctx=None, q=None, limit=1000):
        from asterion.authz.catalog import load_permission_keys

        keys = sorted(await load_permission_keys(session))
        if q and q.strip():
            needle = q.strip().lower()
            keys = [k for k in keys if needle in k.lower()]
        return [{"value": k, "label": k} for k in keys[:limit]]


class TenantMembershipRoleInline(InlineAdmin):
    """User→role assignments for a role, edited inline on the role detail.

    ``membership_id`` references a public ``TenantMembership`` (cross-schema,
    no DB-level FK). The dual-list widget (Theme F) resolves those ids to
    member emails via :meth:`resolve_options`; the standalone
    :class:`TenantMembershipRoleAdmin` keeps the same picker for callers that
    prefer the dedicated table view."""

    model = TenantMembershipRole
    fk_name = "role_id"
    label = "Members"
    fields = ["membership_id"]
    ordering = ["membership_id"]
    extra = 1
    can_delete = True
    widget = "dual_list"
    value_field = "membership_id"

    async def resolve_options(self, *, session, ctx=None, q=None, limit=1000):
        """Member-email options for the cross-schema ``membership_id`` picker.

        ``tenant_memberships`` lives in the **public** schema, so the request
        session's tenant ``search_path`` does NOT scope this join — we restrict
        to the active tenant to avoid offering members of *other* tenants. With
        no tenant in context we offer nothing rather than everyone.
        """
        if ctx is None or ctx.tenant is None:
            return []
        stmt = _membership_email_stmt().where(TenantMembership.tenant_id == ctx.tenant.id)
        if q and q.strip():
            stmt = stmt.where(User.email.ilike(f"%{q.strip()}%"))
        stmt = stmt.order_by(User.email.asc()).limit(limit)
        rows = (await session.execute(stmt)).all()
        return [{"value": str(mid), "label": email} for mid, email in rows]


class TenantRoleAdmin(ModelAdmin):
    model = TenantRole

    label = "Tenant Role"
    label_plural = "Tenant Roles"
    description = "Tenant-local roles used for permission assignment."

    list_display = ["name", "description", "is_system", "created_at"]
    search_fields = ["name", "description"]
    ordering = ["name"]
    readonly_fields = ["id", "created_at", "updated_at"]
    # Permission assignment + user→role assignment are edited inline on the
    # role detail (one "Edit" writes role + permissions + members atomically),
    # replacing the separate "Edit permissions" picker.
    inlines = [TenantRolePermissionInline, TenantMembershipRoleInline]


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
    # Managed through the Tenant Role detail's "Members" inline, so it stays
    # routable (and keeps its email-resolving picker) but off the sidebar.
    show_in_nav = False
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

        ``tenant_memberships`` lives in the **public** schema, so the request
        session's tenant ``search_path`` does NOT scope this join — without an
        explicit ``tenant_id`` filter the picker would offer members of *other*
        tenants (cross-tenant disclosure). We restrict to the active tenant; if
        there is no tenant in context we offer nothing rather than everyone.
        """
        if field != "membership_id":
            return None
        if ctx is None or ctx.tenant is None:
            return []
        stmt = _membership_email_stmt().where(TenantMembership.tenant_id == ctx.tenant.id)
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
        # search_path includes public. One batched query. The rows here already
        # belong to the active tenant's schema, so their membership_ids are
        # tenant-local — but we defensively re-apply the tenant_id filter so a
        # stray cross-tenant membership_id resolves to no label rather than
        # leaking a foreign member's email.
        membership_ids = {o.membership_id for o in objs if o.membership_id is not None}
        if membership_ids:
            stmt = _membership_email_stmt().where(TenantMembership.id.in_(membership_ids))
            if ctx is not None and ctx.tenant is not None:
                stmt = stmt.where(TenantMembership.tenant_id == ctx.tenant.id)
            rows = (await session.execute(stmt)).all()
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


class UserAdmin(ModelAdmin):
    """Built-in admin on the global :class:`User` table.

    Editable but **update-only** (:class:`NoCreateDeletePolicy`): accounts are
    born through invite / ``_members`` (a raw insert here would bypass password
    hashing and produce a login-broken row) and removed through a path that also
    cleans up tenant memberships (a raw delete would orphan them). So the UI
    keeps Edit but hides New / Delete; the policy 403s those at the route too.

    ``hashed_password`` and ``totp_secret`` are in the global protected-field
    set, so they never reach the client at all; the rest of the security-
    sensitive columns are read-only so the detail form shows them disabled.
    """

    model = User

    label = "User"
    label_plural = "Users"
    description = "Global user accounts. Tenant membership is managed separately."

    superadmin_only = True
    policy = NoCreateDeletePolicy()

    list_display = [
        "email",
        "full_name",
        "is_active",
        "is_superadmin",
        "is_service_account",
        "created_at",
    ]
    search_fields = ["email", "full_name"]
    ordering = ["email"]
    readonly_fields = [
        "id",
        "hashed_password",
        "totp_secret",
        "totp_enabled",
        "token_version",
        "created_at",
        "updated_at",
    ]


class TenantAdmin(ModelAdmin):
    """Built-in admin on the global :class:`Tenant` table.

    Editable but **update-only** (:class:`NoCreateDeletePolicy`): a tenant's
    schema is provisioned / torn down by the provisioning path, so creating or
    deleting a row here would leave a tenant with no schema (or a schema with no
    row). ``name`` / ``is_active`` / locale + ``allowed_cidrs`` stay editable;
    ``slug`` and ``schema_name`` are read-only because they map onto the
    physical schema.
    """

    model = Tenant

    label = "Tenant"
    label_plural = "Tenants"
    description = "Tenant rows. Per-tenant schema provisioning is handled by tenancy.bootstrap."

    superadmin_only = True
    policy = NoCreateDeletePolicy()

    list_display = ["slug", "name", "is_active", "schema_name", "created_at"]
    search_fields = ["slug", "name", "schema_name"]
    ordering = ["slug"]
    readonly_fields = ["id", "slug", "schema_name", "created_at", "updated_at"]


class ImpersonationLogAdmin(ModelAdmin):
    """Read-only admin on the global :class:`ImpersonationLog` table.

    Append-only record of superadmin → user impersonation sessions, written by
    :mod:`asterion.root.impersonation`. :class:`ReadOnlyPolicy` blocks
    create/update/delete at the route (so even a caller whose ``admin.*``
    wildcard would match ``admin.impersonation_logs.delete`` cannot mutate it)
    and the UI hides the write controls. ``superadmin_id`` / ``target_user_id``
    resolve to emails and ``tenant_id`` to the tenant slug for readable lists.
    """

    model = ImpersonationLog

    label = "Impersonation Log"
    label_plural = "Impersonation Logs"
    description = "Record of superadmin → user impersonation sessions."

    superadmin_only = True
    policy = ReadOnlyPolicy()

    list_display = [
        "created_at",
        "superadmin_id",
        "target_user_id",
        "tenant_id",
        "revoked_at",
    ]
    search_fields = ["jti"]
    ordering = ["-created_at"]
    readonly_fields = [
        "id",
        "superadmin_id",
        "target_user_id",
        "tenant_id",
        "jti",
        "revoked_at",
        "created_at",
        "updated_at",
    ]

    async def resolve_list_labels(self, objs, *, session, ctx=None):
        # superadmin_id + target_user_id both resolve from one batched
        # user→email map; tenant_id → slug.
        emails = await _email_labels(
            session,
            {o.superadmin_id for o in objs} | {o.target_user_id for o in objs},
        )
        return {
            "superadmin_id": emails,
            "target_user_id": emails,
            "tenant_id": await _tenant_slug_labels(session, {o.tenant_id for o in objs}),
        }


BUILTIN_TENANT_ADMINS: tuple[type[ModelAdmin], ...] = (
    TenantRoleAdmin,
    TenantRolePermissionAdmin,
    TenantMembershipRoleAdmin,
    TenantAuditLogAdmin,
)

#: Admins on framework-owned global (public-schema) tables: ``User`` /
#: ``Tenant`` (update-only) and ``ImpersonationLog`` (read-only). Installed by
#: default alongside the tenant + audit admins so any app with
#: ``enable_builtin_admins=True`` gets a superadmin UI for the core global
#: models for free. All three are overridable — an app that re-registers its
#: own ``User`` / ``Tenant`` / ``ImpersonationLog`` admin wins (the installer
#: skips models already registered).
BUILTIN_GLOBAL_ADMINS: tuple[type[ModelAdmin], ...] = (
    UserAdmin,
    TenantAdmin,
    ImpersonationLogAdmin,
)

#: Read-only admins on framework-owned tables (Roadmap 5.1). Installed
#: by default alongside the tenant admins so any app with
#: ``enable_builtin_admins=True`` gets the Audit-UI for free.
BUILTIN_AUDIT_ADMINS: tuple[type[ModelAdmin], ...] = (AuditLogAdmin,)


__all__ = [
    "BUILTIN_AUDIT_ADMINS",
    "BUILTIN_GLOBAL_ADMINS",
    "BUILTIN_TENANT_ADMINS",
    "AuditLogAdmin",
    "ImpersonationLogAdmin",
    "TenantAdmin",
    "TenantAuditLogAdmin",
    "TenantMembershipRoleAdmin",
    "TenantRoleAdmin",
    "TenantRolePermissionAdmin",
    "UserAdmin",
]
