"""ModelAdmins for the framework's global (public-schema) tables.

Lives in the example for now because the framework does NOT ship these
by default — only the three tenant-scoped RBAC admins live in
``asterion/builtins/admin.py``.

The classes here are written to be self-contained and free of any
example-specific dependencies so that, if the framework ever decides to
ship them, this file can be moved into ``asterion/builtins/admin.py``
(or a sibling module) almost verbatim. Conventions deliberately mirror
``asterion/builtins/admin.py`` — same attribute order, same style of
description, no external imports.

Read-only intent
----------------

``AuditLogAdmin`` and ``ImpersonationLogAdmin`` are append-only records
written by the framework, never edited through the admin. They attach
:class:`~asterion.admin.policy.ReadOnlyPolicy`, which makes the CRUD
router answer 403 to create/update/delete and the contract-driven UI
hide the write buttons — so the rows cannot be mutated or deleted even
by a caller (e.g. a tenant ``owner`` whose ``admin.*`` wildcard would
otherwise match ``admin.impersonation_logs.delete``). Listing every
field in ``readonly_fields`` additionally locks the detail form; that
alone is form-level only (the DELETE endpoint still exists), which is
why the policy — not just the field list — is the actual guard.

Permission catalog side effects
-------------------------------

Registering these admins automatically expands ``PermissionCatalog``
with the corresponding ``admin.<resource>.{list,read,create,update,
delete}`` keys (via the registry → catalog sync in
``tenancy.bootstrap``). For this example that is harmless: the
resources are all public-schema so only superadmins can reach them at
the HTTP layer. The deny-list in ``tenancy.bootstrap`` correctly
excludes ``admin.audit_logs.delete`` and ``admin.users.delete`` from
the default ``admin`` tenant role.
"""

from __future__ import annotations

from sqlalchemy import select

from asterion.admin.policy import ReadOnlyPolicy
from asterion.models import (
    AuditLog,
    ImpersonationLog,
    Tenant,
    TenantMembership,
    User,
)
from asterion.registry import AdminRegistry, ModelAdmin


async def _email_labels(session, user_ids) -> dict[str, str]:
    """Batched ``user_id`` → email map (one query)."""
    ids = {u for u in user_ids if u is not None}
    if not ids:
        return {}
    rows = (await session.execute(select(User.id, User.email).where(User.id.in_(ids)))).all()
    return {str(uid): email for uid, email in rows}


async def _tenant_slug_labels(session, tenant_ids) -> dict[str, str]:
    """Batched ``tenant_id`` → slug map (one query)."""
    ids = {t for t in tenant_ids if t is not None}
    if not ids:
        return {}
    rows = (await session.execute(select(Tenant.id, Tenant.slug).where(Tenant.id.in_(ids)))).all()
    return {str(tid): slug for tid, slug in rows}


class UserAdmin(ModelAdmin):
    model = User

    label = "User"
    label_plural = "Users"
    description = "Global user accounts. Tenant membership is managed separately."

    list_display = ["email", "full_name", "is_active", "is_superadmin", "created_at"]
    search_fields = ["email", "full_name"]
    ordering = ["email"]
    readonly_fields = ["id", "token_version", "created_at", "updated_at"]
    # ``hashed_password`` is already in ``GLOBALLY_PROTECTED`` and is stripped
    # from every contract/CRUD response, so it does not appear in the UI.


class TenantAdmin(ModelAdmin):
    model = Tenant

    label = "Tenant"
    label_plural = "Tenants"
    description = "Tenant rows. Per-tenant schema provisioning is handled by tenancy.bootstrap."

    list_display = ["slug", "name", "is_active", "schema_name", "created_at"]
    search_fields = ["slug", "name", "schema_name"]
    ordering = ["slug"]
    readonly_fields = ["id", "schema_name", "created_at", "updated_at"]


class TenantMembershipAdmin(ModelAdmin):
    model = TenantMembership

    label = "Tenant Membership"
    label_plural = "Tenant Memberships"
    description = (
        "Links a global user to a tenant. Per-tenant role assignment lives in "
        "TenantMembershipRole, inside the tenant schema."
    )

    list_display = ["user_id", "tenant_id", "is_active", "created_at"]
    ordering = ["tenant_id"]
    readonly_fields = ["id", "created_at", "updated_at"]

    async def resolve_list_labels(self, objs, *, session, ctx=None):
        return {
            "user_id": await _email_labels(session, {o.user_id for o in objs}),
            "tenant_id": await _tenant_slug_labels(session, {o.tenant_id for o in objs}),
        }


# Every persistent column listed so the generated form renders disabled
# inputs only. See module docstring for the read-only convention.
_AUDIT_LOG_READONLY = [
    "id",
    "method",
    "path",
    "status_code",
    "actor_user_id",
    "actor_label",
    "tenant_id",
    "resource",
    "record_id",
    "action",
    "changes",
    "ip_address",
    "created_at",
    "updated_at",
]


class AuditLogAdmin(ModelAdmin):
    model = AuditLog

    label = "Audit Log"
    label_plural = "Audit Logs"
    description = "Immutable log of administrative actions. Read-only in the UI."

    # Append-only: ReadOnlyPolicy makes create/update/delete 403 regardless
    # of the caller's permission keys (the readonly_fields below only disable
    # the form). See module docstring.
    policy = ReadOnlyPolicy()

    list_display = ["created_at", "actor_label", "method", "path", "status_code", "resource"]
    search_fields = ["path", "resource", "record_id", "action", "actor_label"]
    ordering = ["-created_at"]
    readonly_fields = _AUDIT_LOG_READONLY


_IMPERSONATION_LOG_READONLY = [
    "id",
    "superadmin_id",
    "target_user_id",
    "tenant_id",
    "jti",
    "revoked_at",
    "created_at",
    "updated_at",
]


class ImpersonationLogAdmin(ModelAdmin):
    model = ImpersonationLog

    label = "Impersonation Log"
    label_plural = "Impersonation Logs"
    description = "Record of superadmin → user impersonation sessions. Read-only in the UI."

    # Append-only: blocks create/update/delete at the route, so a tenant
    # owner's admin.* wildcard cannot delete cross-tenant impersonation
    # records. See module docstring.
    policy = ReadOnlyPolicy()

    list_display = ["created_at", "superadmin_id", "target_user_id", "tenant_id", "revoked_at"]
    search_fields = ["jti"]
    ordering = ["-created_at"]
    readonly_fields = _IMPERSONATION_LOG_READONLY

    async def resolve_list_labels(self, objs, *, session, ctx=None):
        # superadmin_id + target_user_id both resolve from the same batched
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


GLOBAL_ADMINS: tuple[type[ModelAdmin], ...] = (
    UserAdmin,
    TenantAdmin,
    TenantMembershipAdmin,
    AuditLogAdmin,
    ImpersonationLogAdmin,
)


def register_global_admins(registry: AdminRegistry) -> None:
    """Register the global-schema admins on ``registry``.

    Kept separate from any example-specific registration so the call site
    can opt in or out without touching the admin classes themselves.
    """
    for admin_cls in GLOBAL_ADMINS:
        registry.register(admin_cls)


__all__ = [
    "GLOBAL_ADMINS",
    "AuditLogAdmin",
    "ImpersonationLogAdmin",
    "TenantAdmin",
    "TenantMembershipAdmin",
    "UserAdmin",
    "register_global_admins",
]
