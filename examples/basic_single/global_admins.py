"""ModelAdmins for framework-owned global tables (User, AuditLog).

Single-tenant subset of the same idea used by
``examples/multi_tenant/global_admins.py``. Kept in the example because
the framework doesn't ship these by default; structured so the classes
can be moved into ``asterion/builtins/admin.py`` later without
touching the call sites.

``AuditLogAdmin`` expresses read-only intent by listing every field in
``readonly_fields`` — there is no framework-level ``read_only = True``
flag in v1.
"""

from __future__ import annotations

from asterion.models import AuditLog, User
from asterion.registry import AdminRegistry, ModelAdmin


class UserAdmin(ModelAdmin):
    model = User

    label = "User"
    label_plural = "Users"
    description = "Global user accounts."

    list_display = ["email", "full_name", "is_active", "is_superadmin", "created_at"]
    search_fields = ["email", "full_name"]
    ordering = ["email"]
    readonly_fields = ["id", "token_version", "created_at", "updated_at"]
    # ``hashed_password`` is in ``GLOBALLY_PROTECTED`` and is stripped from
    # every contract/CRUD response automatically.


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

    list_display = ["created_at", "actor_label", "method", "path", "status_code", "resource"]
    search_fields = ["path", "resource", "record_id", "action", "actor_label"]
    ordering = ["-created_at"]
    readonly_fields = _AUDIT_LOG_READONLY


GLOBAL_ADMINS: tuple[type[ModelAdmin], ...] = (
    UserAdmin,
    AuditLogAdmin,
)


def register_global_admins(registry: AdminRegistry) -> None:
    for admin_cls in GLOBAL_ADMINS:
        registry.register(admin_cls)


__all__ = [
    "GLOBAL_ADMINS",
    "AuditLogAdmin",
    "UserAdmin",
    "register_global_admins",
]
