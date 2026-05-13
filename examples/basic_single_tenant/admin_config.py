from adminfoundry.admin import admin_site, ModelAdmin
from examples.basic_single_tenant.models import Note


class NoteAdmin(ModelAdmin):
    model = Note
    label = "Note"
    label_plural = "Notes"
    list_display = ["title", "created_at"]
    search_fields = ["title", "content"]
    ordering = ["-created_at"]
    readonly_fields = ["id", "created_at", "updated_at"]


admin_site.register(NoteAdmin())
