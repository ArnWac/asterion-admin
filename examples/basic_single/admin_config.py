"""Admin registration for the single-tenant blog example.

Single-tenant mode (enable_multi_tenant=False) does not install the tenant
RBAC builtins, so the registry only contains what we register here.
"""

from __future__ import annotations

from adminfoundry import AdminRegistry, ModelAdmin
from adminfoundry.actions import BulkDeleteAction
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
    registry.register(PostAdmin)
