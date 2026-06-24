# ModelAdmin API reference

Subclass `asterion.ModelAdmin`, set a few class attributes, register
it. The framework derives CRUD routes, a JSON contract, payload validators,
and a serializer from the declaration.

```python
from asterion import ModelAdmin
from asterion.actions import BulkDeleteAction


class PostAdmin(ModelAdmin):
    model            = Post
    label            = "Post"
    label_plural     = "Posts"
    description      = "Blog posts."

    list_display     = ["id", "title", "published", "created_at"]
    search_fields    = ["title", "body"]
    ordering         = ["-created_at"]

    readonly_fields  = ["id", "created_at", "updated_at"]
    protected_fields = ["internal_token"]

    actions = [BulkDeleteAction()]

    calculated_fields = {
        "word_count": lambda obj: len((obj.body or "").split()),
    }
```

## Attribute reference

| Attribute | Type | Purpose |
|---|---|---|
| `model` | SQLAlchemy mapped class | Required. The DB model to expose. |
| `label`, `label_plural`, `description` | `str \| None` | Human-readable. Default derived from `model.__name__`. |
| `list_display` | `list[str]` | Columns the list-view serializer emits. |
| `search_fields` | `list[str]` | Columns considered by `?search=...` (ILIKE on stringified value). |
| `ordering` | `list[str]` | Default ORDER BY. Prefix `-` for descending. |
| `readonly_fields` | `list[str]` | Rejected on CREATE and UPDATE with `422 validation_error`. |
| `protected_fields` | `list[str]` | Never serialized; never accepted on writes. Adds to framework-wide `GLOBALLY_PROTECTED`. |
| `actions` | `list[AdminAction]` | Bulk actions exposed at `POST /{resource}/_actions/{action_name}`. |
| `calculated_fields` | `dict[str, Callable]` | Read-only computed columns. Surface in contract + serializer. Writes rejected. |
| `filter_fields` | `list[str]` | Columns filterable via `?filter_<name>=<value>` on the list view. |
| `inlines` | `list[InlineAdmin]` | Child models edited inline with the parent record. |
| `policy` | `AdminPolicy \| None` | Object/field-level rules layered on top of permission keys. |
| `fieldsets` | `list[Fieldset]` | Form-layout grouping; rendered as collapsible sections (Roadmap 5.4). |
| `form_layout` | `str` | `"sections"` (default) or `"tabs"` — how `fieldsets` are laid out (Roadmap 5.4). |
| `list_badges` | `dict[str, dict]` | List-view badge styling, e.g. `{"status": {"published": "success"}}` (Roadmap 5.5). |
| `date_hierarchy` | `str \| None` | Date/DateTime column for a year→month→day list filter (Roadmap 5.5). |
| `list_editable` | `list[str]` | Writable `list_display` columns editable inline in the list; saved per-row (Roadmap 5.5). |
| `widgets` | `dict[str, str]` | Per-field widget override, e.g. `{"bio": "textarea"}` (Roadmap 5.4). |
| `field_dependencies` | `dict[str, dict]` | Dependent select choices keyed by a controlling field's value (Roadmap 5.4). |
| `placeholders` | `dict[str, str]` | Per-field placeholder text shown in form inputs (Roadmap 5.4). |
| `field_conditions` | `dict[str, dict]` | Per-field conditional visibility, e.g. `{"vat_id": {"field": "is_business", "equals": True}}` (Roadmap 5.4). |
| `singleton` | `bool` | "Exactly one row per tenant" settings page — create blocked once a row exists, delete blocked; UI jumps straight into the single row's detail. |

Everything older (`field_policies`, `record_filter`, `widget_overrides`,
`inline_fields`, `allow_import`, `requires_approval`, …) was dropped in the
v1 cleanup and is not coming back.

## Built-in admins

Several admins ship pre-registered when `enable_builtin_admins=True` (default).
Each is registered only if the model isn't already in the registry, so an app
can re-register its own variant and win.

Tenant-local (RBAC) admins:

| Class | Resource | Purpose |
|---|---|---|
| `TenantRoleAdmin` | `tenant_roles` | Manage tenant-local roles |
| `TenantRolePermissionAdmin` | `tenant_role_permissions` | Manage role-to-permission-key assignments |
| `TenantMembershipRoleAdmin` | `tenant_membership_roles` | Manage which membership has which role |

Global (public-schema) admins — `superadmin_only`, so a tenant-scoped caller
with an `admin.*` grant cannot reach them and read across tenants:

| Class | Resource | Write scope |
|---|---|---|
| `UserAdmin` | `users` | Update-only (no create/delete; accounts come from invite, password/totp hidden) |
| `TenantAdmin` | `tenants` | Update-only (no create/delete; `slug`/`schema_name` read-only) |
| `ImpersonationLogAdmin` | `impersonation_logs` | Read-only |
| `AuditLogAdmin` | `audit_logs` | Read-only |

## Singleton admins

For the "exactly one config/profile row per tenant" pattern (organization
profile, settings, branding), set `singleton = True`:

```python
class OrganizationProfileAdmin(ModelAdmin):
    model = OrganizationProfile
    singleton = True
```

The framework then:

- allows **create** only while the (tenant-scoped) table is empty and blocks
  **delete** — a `403` at the route, mirrored in `capabilities.create` /
  `capabilities.delete` so the built-in UI hides the New/Delete controls;
- stamps `singleton: true` on the contract so the UI renders a settings page —
  the nav entry jumps straight into the single row's detail/edit instead of a
  one-row list (create form when no row exists yet);
- counts rows through the **request session**, so independence is **per-tenant**
  on schema-per-tenant Postgres — there is **no** DB UNIQUE/constraint (a global
  one would wrongly cap the table at one row across all tenants on a shared
  SQLite namespace). This is an admin-presentation-+-policy feature, not a
  data-integrity guarantee.

An explicitly set `policy` takes precedence: `singleton` only supplies the
default create/delete behavior when no custom policy owns those decisions.

## Calculated fields

```python
class ArticleAdmin(ModelAdmin):
    model = Article
    list_display = ["id", "title", "word_count"]
    calculated_fields = {
        "word_count":   lambda obj: len((obj.body or "").split()),
        "display_name": lambda obj: f"[{obj.id}] {obj.title}",
    }
```

- Each callable receives the ORM instance.
- Values surface in list AND detail serializer output.
- The contract marks them `calculated=True, read_only=True`.
- Writes are rejected with 422.
- Exceptions inside a callable degrade to `null` in the response (no 500).

## Actions

Subclass `asterion.actions.AdminAction`, set `name` + `label`,
implement an async `execute(records, session, user) -> dict`. Add the
instance to a `ModelAdmin.actions` list.

```python
from asterion.actions import AdminAction


class MarkPublished(AdminAction):
    name = "publish"
    label = "Mark as published"

    async def execute(self, records, session, user):
        for r in records:
            r.published = True
        await session.flush()      # NOT commit; transaction is owned by the router
        return {"affected": len(records), "summary": f"Published {len(records)} post(s)."}


class PostAdmin(ModelAdmin):
    model = Post
    actions = [MarkPublished()]
```

The endpoint is `POST /api/v1/admin/posts/_actions/publish` with body
`{"ids": [...]}`. The required permission is `admin.posts.publish`.

`actions` always includes the implicit `delete` permission through
`BulkDeleteAction` if you add it.

## Registration

```python
from asterion import create_admin, CoreAdminConfig

def register(registry):
    registry.register(PostAdmin)
    registry.register(CommentAdmin)

app = create_admin(
    config=CoreAdminConfig.from_env(),
    register=register,
)
```

After registering models, sync the permission catalog so default tenant
roles have something to grant:

```bash
ASTERION_APP=app:app asterion permissions sync
```
