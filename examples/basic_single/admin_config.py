"""Admin registration for the single-tenant blog example.

``PostAdmin`` is deliberately exhaustive: it exercises *every*
``ModelAdmin`` attribute so the example doubles as a feature tour. Each
attribute is grouped + commented below. Global framework admins
(``UserAdmin`` etc.) live in ``global_admins.py``.

Tenant RBAC admins are intentionally NOT registered here — in
single-tenant mode (``enable_multi_tenant=False``) the CRUD permission
gate is a no-op, so exposing them would imply an access-control system
this mode doesn't have.
"""

from __future__ import annotations

from typing import Any

from adminfoundry import AdminRegistry, ModelAdmin
from adminfoundry.actions import BulkDeleteAction
from adminfoundry.admin.context import AdminContext
from adminfoundry.admin.fieldset import Fieldset
from adminfoundry.admin.inline import InlineAdmin
from adminfoundry.admin.policy import AdminPolicy, FieldPermission
from examples.basic_single.global_admins import register_global_admins
from examples.basic_single.models import Comment, Post

# --- calculated fields (read-only, derived at serialization time) ---


def _word_count(obj: Post) -> int:
    return len((obj.content or "").split())


def _read_time(obj: Post) -> str:
    minutes = max(1, round(_word_count(obj) / 200))
    return f"{minutes} min"


# --- per-field policy: internal_notes is read-only for non-superadmins ---


class PostPolicy(AdminPolicy):
    async def field_permission(self, field: str, obj: Any, ctx: AdminContext) -> FieldPermission:
        if field == "internal_notes" and not getattr(ctx, "is_superadmin", False):
            return FieldPermission.READ
        return FieldPermission.WRITE


# --- inline child rows edited inside the Post form ---


class CommentInline(InlineAdmin):
    model = Comment
    fk_name = "post_id"
    fields = ["author", "body", "is_public"]
    readonly_fields = ["created_at"]
    ordering = ["created_at"]
    extra = 1
    can_delete = True


class PostAdmin(ModelAdmin):
    model = Post
    label = "Post"
    label_plural = "Posts"
    description = "Blog posts — a tour of the full ModelAdmin surface."

    # --- list view ---
    list_display = ["title", "author", "status", "category", "published", "created_at"]
    search_fields = ["title", "summary", "content", "author"]
    ordering = ["-created_at"]
    filter_fields = ["status", "category", "published"]
    date_hierarchy = "created_at"
    list_badges = {
        "status": {
            "draft": "neutral",
            "review": "info",
            "published": "success",
            "archived": "danger",
        },
    }
    list_editable = ["author", "published"]  # inline text + checkbox

    # --- field access ---
    readonly_fields = ["id", "created_at", "updated_at"]
    protected_fields = ["api_secret"]
    policy = PostPolicy()

    # --- form layout ---
    form_layout = "tabs"
    fieldsets = [
        Fieldset(
            "Content", fields=["title", "summary", "content"], description="What readers see."
        ),
        Fieldset(
            "Classification",
            fields=["status", "category", "subcategory", "published", "published_at"],
        ),
        Fieldset("Internal", fields=["internal_notes", "api_secret"], collapsed=True),
    ]
    placeholders = {"title": "e.g. Hello, world", "summary": "One-sentence teaser"}
    widgets = {"content": "textarea", "internal_notes": "textarea"}
    field_conditions = {
        "published_at": {"field": "status", "equals": "published"},
        "summary": {"field": "status", "in": ["review", "published"]},
    }
    field_dependencies = {
        "subcategory": {
            "field": "category",
            "options": {
                "engineering": ["backend", "frontend", "infra"],
                "product": ["roadmap", "research"],
            },
        },
    }

    # --- behaviour ---
    actions = [BulkDeleteAction()]
    inlines = [CommentInline]
    calculated_fields = {"word_count": _word_count, "read_time": _read_time}

    async def before_create(self, data: dict[str, Any], ctx: AdminContext) -> dict[str, Any]:
        """Lifecycle hook example: tidy the author field before validation."""
        if isinstance(data.get("author"), str):
            data["author"] = data["author"].strip()
        return data


def register(registry: AdminRegistry) -> None:
    register_global_admins(registry)
    registry.register(PostAdmin)
