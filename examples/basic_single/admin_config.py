"""Admin registration for the single-tenant blog example.

Everything visible in the admin UI is registered here explicitly —
``enable_builtin_admins=False`` in app.py, so nothing is auto-installed
behind the scenes.

Registered admins:

* ``PostAdmin`` — the demo's app-specific model.
* ``UserAdmin``, ``AuditLogAdmin`` — global framework tables, defined in
  ``global_admins.py`` next to this file. Kept out of ``builtins/`` for
  now; the file is structured so they can be moved in later without
  touching call sites.

Tenant RBAC admins (``TenantRoleAdmin`` etc.) are intentionally NOT
registered here. In single-tenant mode (``enable_multi_tenant=False``)
the CRUD permission gate is a no-op — the rows in those tables are
never consulted at request time — so registering them would expose UI
that suggests an access-control system that does not exist in this
mode. v1 has no global-role concept; authorization for non-superadmins
in single-tenant mode is binary (any valid JWT can hit /admin/*).
"""

from __future__ import annotations

from adminfoundry import AdminRegistry, ModelAdmin
from adminfoundry.actions import BulkDeleteAction
from examples.basic_single.global_admins import register_global_admins
from examples.basic_single.models import Post


def _word_count(obj: Post) -> int:
    return len((obj.content or "").split())


def _read_time(obj: Post) -> str:
    minutes = max(1, round(_word_count(obj) / 200))
    return f"{minutes} min"


class PostAdmin(ModelAdmin):
    model = Post
    label = "Post"
    label_plural = "Posts"
    description = "Blog posts"

    list_display = ["title", "author", "word_count", "read_time", "published", "created_at"]
    search_fields = ["title", "content", "author"]
    ordering = ["-created_at"]
    readonly_fields = ["id", "created_at", "updated_at"]

    actions = [BulkDeleteAction()]

    # Calculated (read-only) columns derived from the row at serialization time.
    calculated_fields = {
        "word_count": _word_count,
        "read_time": _read_time,
    }


def register(registry: AdminRegistry) -> None:
    register_global_admins(registry)
    registry.register(PostAdmin)
