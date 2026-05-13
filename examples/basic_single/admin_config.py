"""Admin registrations for the single-tenant blog example."""
from adminfoundry import (
    ModelAdmin, admin_site,
    BulkDeleteAction, DeactivateUsersAction, ActivateUsersAction,
)
from adminfoundry.auth import hash_password
from adminfoundry.models import User

from examples.basic_single.models import Post


def _word_count(obj: Post) -> int:
    return len((obj.content or "").split())


def _read_time(obj: Post) -> str:
    minutes = max(1, round(_word_count(obj) / 200))
    return f"{minutes} min"


def _excerpt(obj: Post) -> str:
    body = (obj.content or "").strip()
    return body[:100] + ("..." if len(body) > 100 else "")


class PostAdmin(ModelAdmin):
    model           = Post
    label           = "Post"
    label_plural    = "Posts"
    description     = "Blog posts"
    list_display    = ["title", "author", "word_count", "read_time", "published", "created_at"]
    search_fields   = ["title", "content", "author"]
    filter_fields   = ["published"]
    ordering        = ["-created_at"]
    readonly_fields = ["id", "created_at", "updated_at"]
    actions         = [BulkDeleteAction()]
    fieldsets = [
        ("Content",    ["title", "content"]),
        ("Publishing", ["author", "published"]),
    ]
    computed_fields = {
        "word_count": _word_count,
        "read_time":  _read_time,
        "excerpt":    _excerpt,
    }


class UserAdmin(ModelAdmin):
    model           = User
    label           = "User"
    label_plural    = "Users"
    description     = "Registered users"
    list_display    = ["email", "full_name", "is_active", "is_superadmin"]
    search_fields   = ["email", "full_name"]
    filter_fields   = ["is_active", "is_superadmin"]
    ordering        = ["email"]
    readonly_fields = ["id", "created_at", "updated_at"]
    tenant_scoped   = False
    extra_create_fields = {"set_password": str}
    actions = [DeactivateUsersAction(), ActivateUsersAction(), BulkDeleteAction()]

    @classmethod
    def before_create(cls, data: dict) -> dict:
        plain = data.pop("set_password", None)
        if plain:
            data["hashed_password"] = hash_password(plain)
        return data


admin_site.register(PostAdmin())
admin_site.register(UserAdmin())
