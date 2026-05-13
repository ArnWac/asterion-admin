# ModelAdmin Configuration

## Minimal Example

```python
from adminfoundry import ModelAdmin, admin_site

class ArticleAdmin(ModelAdmin):
    model = Article
    list_display = ["title", "published", "created_at"]
    search_fields = ["title"]
    filter_fields = ["published"]
    readonly_fields = ["id", "created_at", "updated_at"]

admin_site.register(ArticleAdmin())
```

## All Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `model` | `type` | — | SQLAlchemy model class (**required**) |
| `label` | `str \| None` | model name | Singular display name |
| `label_plural` | `str \| None` | label + "s" | Plural display name |
| `description` | `str \| None` | `None` | Shown in UI as subtitle |
| `list_display` | `list[str]` | `[]` | Columns shown in the list view |
| `search_fields` | `list[str]` | `[]` | Fields searched via `?search=` |
| `filter_fields` | `list[str]` | `[]` | Boolean/choice sidebar filters |
| `range_filter_fields` | `list[str]` | `[]` | `?field__gte=X&field__lte=Y` filters |
| `enum_filter_fields` | `list[str]` | `[]` | `?field__in=a,b,c` multi-value filters |
| `ordering` | `list[str]` | `[]` | Default sort (`-field` for DESC) |
| `readonly_fields` | `list[str]` | `[]` | Cannot be mutated; 422 on attempt |
| `protected_fields` | `list[str]` | `[]` | Hidden from all schemas (adds to GLOBALLY_PROTECTED) |
| `tenant_scoped` | `bool` | `False` | Filter by `tenant_id` in multi-tenant mode |
| `global_only_in_root_panel` | `bool` | `False` | Root panel shows `WHERE tenant_id IS NULL` instead of all rows |
| `admin_only` | `bool` | `True` | Requires superadmin; set `False` for role-based access |
| `access_roles` | `list[str]` | `[]` | Role names that grant access when `admin_only=False` |
| `field_policies` | `dict` | `{}` | `{field: {"view_roles": [...], "edit_roles": [...]}}`  |
| `record_filter` | `callable \| None` | `None` | `(user) -> WHERE clause \| None` |
| `record_access` | `callable \| None` | `None` | `(user, record) -> bool` |
| `action_policies` | `dict` | `{}` | `{action_name: {"roles": [...]}}` |
| `actions` | `list` | `[]` | `AdminAction` instances |
| `async_actions` | `list[str]` | `[]` | Action names executed asynchronously (via jobs) |
| `requires_approval` | `bool` | `False` | Changes require workflow approval |
| `lookup_field` | `str \| None` | `list_display[0]` | Field used as label in relation pickers |
| `inline_fields` | `list[str]` | `[]` | Relationship attrs editable inline |
| `list_editable` | `list[str]` | `[]` | Fields editable directly in the list |
| `create_redirect` | `str` | `"list"` | Where to go after create: `"list"` or `"detail"` |
| `field_choices_urls` | `dict` | `{}` | `{field: url}` — renders a `<select>` |
| `permission_matrix` | `bool` | `False` | Render permission matrix section (for Role-like models) |
| `allow_delete` | `bool` | `True` | `False` blocks deletion at the API level |
| `soft_delete` | `bool` | `False` | DELETE sets `deleted_at` instead of removing the row |
| `allow_import` | `bool` | `False` | Enable CSV import endpoint and button |
| `extra_create_fields` | `dict` | `{}` | Virtual create-only fields: `{"field": type}` |
| `fieldsets` | `list \| None` | `None` | `[("Section", ["field1", "field2"]), …]` |
| `widget_overrides` | `dict` | `{}` | `{field: "image" \| "file"}` |
| `computed_fields` | `dict` | `{}` | `{name: callable(obj) -> value}` — read-only virtual columns |

## Lifecycle Hooks

```python
@classmethod
def before_create(cls, data: dict) -> dict:
    """Transform validated create data before the model instance is built."""
    ...

@classmethod
def before_update(cls, data: dict, existing) -> dict:
    """Transform validated update data before applying to the existing instance."""
    ...

def field_permission(self, user, field_name: str, record) -> FieldPolicy | None:
    """Return a FieldPolicy based on current record state, or None to use field_policies."""
    ...
```

## AdminAction

```python
from adminfoundry.admin.actions import AdminAction

class PublishAction(AdminAction):
    name = "publish"
    label = "Publish selected"
    danger = False
    confirm = True
    bulk = True
    single = True

    async def execute(self, objects, db, user):
        for obj in objects:
            obj.published = True
        await db.commit()
        return {"summary": f"{len(objects)} published"}
```
