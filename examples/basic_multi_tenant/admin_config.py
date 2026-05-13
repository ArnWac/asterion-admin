from adminfoundry.admin import admin_site, ModelAdmin
from adminfoundry.models.tenant import Tenant
from examples.basic_multi_tenant.models import Task


class TaskAdmin(ModelAdmin):
    model = Task
    label = "Task"
    label_plural = "Tasks"
    list_display = ["title", "created_at"]
    search_fields = ["title"]
    ordering = ["-created_at"]
    readonly_fields = ["id", "created_at", "updated_at", "tenant_id"]
    tenant_scoped = True


class TenantAdmin(ModelAdmin):
    model = Tenant
    label = "Tenant"
    label_plural = "Tenants"
    list_display = ["name", "slug", "is_active"]
    search_fields = ["name", "slug"]
    filter_fields = ["is_active"]
    ordering = ["slug"]
    readonly_fields = ["id", "created_at", "updated_at"]


admin_site.register(TaskAdmin())
admin_site.register(TenantAdmin())
