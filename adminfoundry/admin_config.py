"""
Admin registrations — import this module to activate them.

Note: admin CRUD for User is read/update only — use POST /api/v1/users to
create users (the admin create path lacks the password hashing step).
"""
from adminfoundry.admin import admin_site, ModelAdmin
from adminfoundry.admin.actions import AdminAction
from adminfoundry.auth import hash_password
from adminfoundry.models.audit_log import AuditLog
from adminfoundry.models.role import Role
from adminfoundry.models.role_permission import RolePermission  # noqa: F401 — registers table with Base.metadata
from adminfoundry.models.tenant import Tenant
from adminfoundry.models.user import User
from adminfoundry.settings import settings


class BulkDeleteAction(AdminAction):
    name = "delete"
    label = "Delete selected"
    danger = True
    confirm = True
    bulk = True
    single = False

    async def execute(self, objects, db, user):
        count = len(objects)
        for obj in objects:
            await db.delete(obj)
        await db.commit()
        return {"summary": f"Deleted {count} record(s)", "affected": count}


class DeactivateUsersAction(AdminAction):
    name = "deactivate"
    label = "Deactivate selected"
    danger = True
    confirm = True
    bulk = True
    single = True

    async def execute(self, objects, db, user):
        for obj in objects:
            obj.is_active = False
        await db.commit()
        return {"summary": f"Deactivated {len(objects)} user(s)", "affected": len(objects)}


class DisableTenantAction(AdminAction):
    name = "disable"
    label = "Disable tenant"
    danger = True
    confirm = True
    bulk = False
    single = True

    async def execute(self, objects, db, user):
        for obj in objects:
            obj.is_active = False
        await db.commit()
        return {"summary": f"Disabled {len(objects)} tenant(s)", "affected": len(objects)}


class UserAdmin(ModelAdmin):
    model = User
    label = "User"
    label_plural = "Users"
    description = "Registered application users"
    list_display = ["email", "full_name", "is_active", "is_superadmin"]
    search_fields = ["email", "full_name"]
    filter_fields = ["is_active", "is_superadmin"]
    ordering = ["email"]
    readonly_fields = ["id", "created_at", "updated_at"]
    # hashed_password is globally protected — no need to list it here
    # Users are global — tenant membership is handled via tenant-scoped Profiles in the app
    protected_fields = ["tenant_id"]
    tenant_scoped = False
    extra_create_fields = {"set_password": str}

    @classmethod
    def before_create(cls, data: dict) -> dict:
        plain = data.pop("set_password", None)
        if plain:
            data["hashed_password"] = hash_password(plain)
        return data
    actions = [DeactivateUsersAction(), BulkDeleteAction()]


class RoleAdmin(ModelAdmin):
    model = Role
    label = "Permission Group"
    label_plural = "Permissions"
    description = "Permission groups assignable to users — CRUD capabilities configured below"
    list_display = ["name", "description", "created_at"]
    search_fields = ["name"]
    ordering = ["name"]
    readonly_fields = ["id", "created_at", "updated_at"]
    protected_fields = ["tenant_id"]
    tenant_scoped = True
    global_only_in_root_panel = True
    permission_matrix = True
    create_redirect = "detail"
    actions = [BulkDeleteAction()]


class TenantAdmin(ModelAdmin):
    model = Tenant
    label = "Tenant"
    label_plural = "Tenants"
    description = "Tenant organisations in multi-tenant mode"
    list_display = ["name", "slug", "is_active", "timezone", "language"]
    search_fields = ["name", "slug"]
    filter_fields = ["is_active"]
    ordering = ["slug"]
    readonly_fields = ["id", "created_at", "updated_at"]
    actions = [DisableTenantAction()]


class AuditLogAdmin(ModelAdmin):
    model = AuditLog
    label = "Audit Log"
    label_plural = "Audit Logs"
    description = "Immutable record of all admin actions"
    list_display = ["created_at", "actor", "action", "method", "path", "status_code", "object_id"]
    search_fields = ["actor", "path", "object_id", "action"]
    filter_fields = ["action", "method", "status_code"]
    range_filter_fields = ["created_at"]
    ordering = ["-created_at"]
    readonly_fields = ["id", "created_at", "updated_at", "method", "path", "status_code",
                       "user_id", "tenant_id", "action", "object_id", "actor", "changes"]
    tenant_scoped = False  # B2C: audit logs are superadmin-only, not visible to tenants
    allow_delete = False
    actions = []


admin_site.register(UserAdmin())
admin_site.register(RoleAdmin())
admin_site.register(AuditLogAdmin())
if settings.MULTI_TENANT:
    admin_site.register(TenantAdmin())
