# The ModelAdmin reference

The `ModelAdmin` class is the representation of a model in the admin. It is the
single declaration from which the framework derives everything a client needs:
the CRUD routes, a JSON **contract** (form/field/list metadata), the payload
validators, and the serializer. You write a subclass, set a few class
attributes, and register it.

```python
from asterion import ModelAdmin
from asterion.actions import BulkDeleteAction


class PostAdmin(ModelAdmin):
    model = Post

    label = "Post"
    label_plural = "Posts"
    description = "Blog posts."

    list_display = ["id", "title", "published", "created_at"]
    search_fields = ["title", "body"]
    ordering = ["-created_at"]

    readonly_fields = ["id", "created_at", "updated_at"]
    protected_fields = ["internal_token"]

    actions = [BulkDeleteAction()]

    calculated_fields = {
        "word_count": lambda obj: len((obj.body or "").split()),
    }
```

Register it (see [Registering ModelAdmin objects](#registering-modeladmin-objects)),
and the resource is served under the admin API prefix (`/api/v1/admin` by
default):

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/posts` | List (paginated, searchable, filterable, orderable) |
| `POST` | `/posts` | Create |
| `GET` | `/posts/{id}` | Read one |
| `PATCH` | `/posts/{id}` | Update (partial) |
| `DELETE` | `/posts/{id}` | Delete |
| `GET` | `/posts/_options/{field}` | Foreign-key dropdown options |
| `POST` | `/posts/_actions/{name}` | Run a bulk [action](#adminaction-objects) |
| `GET` | `/_contract/posts` | The resource's [contract](#the-admin-contract) |

> **Note.** Everything older than the v1 API surface (`field_policies`,
> `record_filter`, `widget_overrides`, `inline_fields`, `allow_import`,
> `requires_approval`, …) was removed in the v1 cleanup and is not coming back.

---

## ModelAdmin options

The options below are all optional except `model`.

### `ModelAdmin.model`

**Required.** The SQLAlchemy mapped class to expose.

```python
class PostAdmin(ModelAdmin):
    model = Post
```

The resource name (the path segment, the contract `resource`, and the prefix of
every permission key) is the model's `__tablename__`. Whether a model is
*tenant-scoped* or *global* is derived from its declarative base — a
`TenantModel` (subclass of `TenantBase`) is `"tenant"`, everything else is
`"global"`. See [Multi-tenancy](tenancy.md) for what that scope changes.

### `ModelAdmin.label` / `ModelAdmin.label_plural` / `ModelAdmin.description`

Human-readable strings surfaced in the contract for the UI.

* `label` — singular display name. Defaults to `model.__name__`.
* `label_plural` — plural display name. Defaults to `f"{label}s"`.
* `description` — a one-line blurb shown under the resource title. Defaults to
  `None`.

```python
class PostAdmin(ModelAdmin):
    model = Post
    label = "Article"
    label_plural = "Articles"
    description = "Long-form editorial content."
```

### `ModelAdmin.list_display`

Set `list_display` to control which columns the list-view serializer emits and
in which order.

```python
class PostAdmin(ModelAdmin):
    list_display = ["id", "title", "published", "created_at"]
```

Each entry is a model column name or a [calculated field](#modeladmincalculated_fields).
An empty list (the default) lets the serializer fall back to the model's own
columns. Columns that are [protected](#modeladminprotected_fields) are never
emitted even if listed.

### `ModelAdmin.ordering`

Set `ordering` to the default `ORDER BY` for the list view. Prefix a name with
`-` for descending order.

```python
class PostAdmin(ModelAdmin):
    ordering = ["-created_at", "title"]
```

A caller may override this per request with `?ordering=<col>` (or
`?ordering=-<col>`); the request value wins when present.

### `ModelAdmin.search_fields`

Set `search_fields` to enable the `?search=<term>` query parameter on the list
view. The term is matched case-insensitively (`ILIKE`) against the stringified
value of each listed column, combined with `OR`.

```python
class PostAdmin(ModelAdmin):
    search_fields = ["title", "body"]
```

With no `search_fields`, `?search=` is ignored.

### `ModelAdmin.filter_fields`

Set `filter_fields` to the columns that may be filtered through query
parameters. The list view then accepts `?filter_<name>=<value>` for any listed
column and applies it as `column == value` with type coercion.

```python
class PostAdmin(ModelAdmin):
    filter_fields = ["status", "author_id"]
    # GET /posts?filter_status=published&filter_author_id=42
```

A filter on a column that is not declared here is rejected with
`422 Unprocessable Entity` — filtering is opt-in per column. The contract
surfaces each filterable column with its wire type so the UI can pick a widget
(text input, boolean dropdown, date picker).

### `ModelAdmin.date_hierarchy`

Set `date_hierarchy` to the name of a `Date` / `DateTime` column to offer a
year → month → day drill-down over the list.

```python
class PostAdmin(ModelAdmin):
    date_hierarchy = "published_at"
    # GET /posts?dh=2026          → that year
    # GET /posts?dh=2026-06       → that month
    # GET /posts?dh=2026-06-24    → that day
```

If the named column is missing or is not a date type, the hint is dropped from
the contract (degrades to "no drill-down") rather than shipping something the
UI can't use.

### `ModelAdmin.list_editable`

Set `list_editable` to the subset of `list_display` columns the UI may edit
inline, directly in the list rows.

```python
class PostAdmin(ModelAdmin):
    list_display = ["id", "title", "status"]
    list_editable = ["status"]
```

Edits are saved through the normal per-row update endpoint, so all validation,
permission, policy, and audit rules apply unchanged. A name that is not in
`list_display`, or that maps to a primary key / read-only / calculated /
protected column, is dropped by the contract builder so the UI never offers an
editor the update endpoint would reject.

### `ModelAdmin.list_badges`

Set `list_badges` to render specific cell values as colored badges instead of
plain text.

```python
class PostAdmin(ModelAdmin):
    list_badges = {
        "status": {
            "published": "success",
            "draft": "neutral",
            "archived": "danger",
        },
    }
```

The shape is `{column: {value: style}}`. `style` must be one of `neutral`,
`success`, `warning`, `danger`, `info` — unknown styles are dropped. Values are
matched against the rendered cell text, so ints, bools, and enums work too.

### `ModelAdmin.show_in_nav`

Set `show_in_nav = False` to hide a resource from the admin sidebar while
keeping it fully routable (CRUD, contract, permission keys all still work).

```python
class TenantRolePermissionAdmin(ModelAdmin):
    show_in_nav = False  # edited through the per-role permission picker, not a list
```

Use this for tables that are managed through a dedicated UI rather than the
generic list. Defaults to `True`.

### `ModelAdmin.readonly_fields`

Set `readonly_fields` to columns that are shown but never accepted on writes. A
create or update payload that includes one is rejected with a `422`
validation error.

```python
class PostAdmin(ModelAdmin):
    readonly_fields = ["id", "created_at", "updated_at"]
```

The framework already treats primary keys and the auto-managed columns (`id`,
`created_at`, `updated_at`, `created_by`, `updated_by`, `deleted_at`) as
read-only; listing them is harmless and explicit.

### `ModelAdmin.protected_fields`

Set `protected_fields` to columns that must never be serialized and never be
accepted on writes — they are *hidden* everywhere (list, detail, contract,
create/update schemas). Treat a protected field as if it did not exist for the
client.

```python
class PostAdmin(ModelAdmin):
    protected_fields = ["internal_token"]
```

These add to the framework-wide protected set. The default set already hides
secret-bearing columns (`hashed_password`, `password`, `totp_secret`,
`shared_secret`, …) on every model, so a typo can never leak a credential. The
combined set for an admin is available as `admin.all_protected`.

### `ModelAdmin.calculated_fields`

Set `calculated_fields` to a mapping of name → callable for read-only columns
computed in Python rather than stored on the model.

```python
class ArticleAdmin(ModelAdmin):
    model = Article
    list_display = ["id", "title", "word_count"]
    calculated_fields = {
        "word_count":   lambda obj: len((obj.body or "").split()),
        "display_name": lambda obj: f"[{obj.id}] {obj.title}",
    }
```

* Each callable receives the ORM instance.
* Values appear in **both** the list and the detail serializer output.
* The contract marks them `calculated=True, read_only=True`.
* Writes that target a calculated field are rejected with `422`.
* An exception inside a callable degrades to `null` in the response — never a
  `500`.

### `ModelAdmin.fieldsets`

Set `fieldsets` to group form fields into labeled sections. Each entry is a
[`Fieldset`](#fieldset-objects).

```python
from asterion.admin import Fieldset

class PostAdmin(ModelAdmin):
    fieldsets = [
        Fieldset("Content", fields=["title", "slug", "body"]),
        Fieldset("Publishing", fields=["status", "published_at"]),
        Fieldset("SEO", fields=["seo_title", "seo_description"], collapsed=True),
    ]
```

The contract builder validates each section against the model: fields that
aren't real columns (and aren't calculated) are dropped, protected fields are
dropped, and duplicates within a section are removed — a misconfigured fieldset
degrades to a partial render rather than a `500`.

### `ModelAdmin.form_layout`

Set `form_layout` to choose how `fieldsets` are laid out: `"sections"` (the
default — collapsible blocks) or `"tabs"` (a tab bar, one tab per fieldset).

```python
class PostAdmin(ModelAdmin):
    form_layout = "tabs"
```

Ignored when no fieldsets are declared. An unrecognized value falls back to
`"sections"`.

### `ModelAdmin.widgets`

Set `widgets` to override the form widget the adapter would otherwise pick for a
field.

```python
class PostAdmin(ModelAdmin):
    widgets = {
        "body": "textarea",
        "category_id": "foreign_key",
    }
```

The built-in UI understands `"textarea"`, `"select"` (pair with `choices`), and
`"foreign_key"` (pair with [`resolve_fk_options`](#resolve_fk_options) for
columns with no DB-level foreign key). Unknown widget names pass through for a
custom client to interpret.

### `ModelAdmin.placeholders`

Set `placeholders` to per-field placeholder text shown in empty form inputs.

```python
class PostAdmin(ModelAdmin):
    placeholders = {"slug": "auto-generated from title if left blank"}
```

Fields without an entry get `None` (the renderer shows no placeholder).

### `ModelAdmin.field_conditions`

Set `field_conditions` to show a field only while another field's value
satisfies a rule.

```python
class OrderAdmin(ModelAdmin):
    field_conditions = {
        "vat_id":        {"field": "is_business", "equals": True},
        "shipping_note": {"field": "ship_method", "in": ["air", "sea"]},
    }
```

Each rule carries `field` plus exactly one of `equals` / `in`. The dependent
field is hidden and dropped from the submitted payload while the rule is not
satisfied, so conditionally hidden fields should be nullable. A rule that
references a non-existent field is dropped (degrades to "always visible").

### `ModelAdmin.field_dependencies`

Set `field_dependencies` to narrow a select field's choices based on a
controlling field's current value.

```python
class AddressAdmin(ModelAdmin):
    field_dependencies = {
        "state": {
            "field": "country",
            "options": {"US": ["CA", "NY"], "DE": ["BY", "BE"]},
        },
    }
```

Only meaningful for fields rendered as a `<select>`. A malformed rule, or one
whose controlling field doesn't exist, is dropped (degrades to "static
choices").

### `ModelAdmin.display_field`

Set `display_field` to the column used as this model's human-readable label when
it is the *target* of a foreign-key picker on another form.

```python
class CategoryAdmin(ModelAdmin):
    model = Category
    display_field = "name"
```

When unset, the framework resolves a label column automatically (via
[`label_field`](#computed-properties)): it tries a small list of conventional
names (`name`, `title`, `label`, `display_name`, `email`, `slug`, `username`,
`code`, `key`), then the first non-PK column in `list_display`, then the primary
key.

### `ModelAdmin.actions`

Set `actions` to a list of [`AdminAction`](#adminaction-objects) instances —
bulk operations exposed at `POST /{resource}/_actions/{name}`.

```python
from asterion.actions import BulkDeleteAction

class PostAdmin(ModelAdmin):
    actions = [BulkDeleteAction(), MarkPublished()]
```

Each action's `name` becomes part of its permission key
(`admin.<resource>.<name>`). The contract lists only the actions the current
caller is permitted to run.

### `ModelAdmin.inlines`

Set `inlines` to edit child rows alongside the parent record. Each entry is an
[`InlineAdmin`](#inlineadmin-objects) subclass or instance.

```python
class PostAdmin(ModelAdmin):
    inlines = [CommentInline]
```

### `ModelAdmin.policy`

Set `policy` to an [`AdminPolicy`](#adminpolicy-objects) instance to layer
object-level and field-level rules on top of the permission-key checks. `None`
(the default) means "permission keys alone decide".

```python
class OrderAdmin(ModelAdmin):
    policy = OwnerOnlyPolicy()
```

A policy can only ever *tighten* access — it can hide rows, forbid an operation,
or downgrade a field to read-only / hidden, but it can never grant access the
permission keys didn't.

### `ModelAdmin.superadmin_only`

Set `superadmin_only = True` to restrict every CRUD route for this admin to
superadmins, regardless of the caller's permission keys.

```python
class UserAdmin(ModelAdmin):
    model = User
    superadmin_only = True
```

The framework already requires a superadmin in *no-tenant* (root) scope, but
inside a tenant a caller with an `admin.*` grant would otherwise reach a
global / public-schema resource and read across tenants. Set this on admins for
public-schema models that must stay superadmin-only everywhere (the built-in
`User` / `Tenant` / `ImpersonationLog` admins do). Defaults to `False`.

### `ModelAdmin.singleton`

Set `singleton = True` for the "exactly one row per tenant" pattern — an
organization profile, a settings record, branding.

```python
class OrganizationProfileAdmin(ModelAdmin):
    model = OrganizationProfile
    singleton = True
```

When `singleton` is `True`:

* **create** is allowed only while the (tenant-scoped) table is empty, and
  **delete** is blocked — a `403` at the route, mirrored in
  `capabilities.create` / `capabilities.delete` so the UI hides the New / Delete
  controls;
* the contract carries `singleton: true`, so the UI renders a settings page —
  the nav entry jumps straight into the single row's detail/edit instead of a
  one-row list (a create form when no row exists yet);
* rows are counted through the **request session**, so independence is
  **per-tenant** on schema-per-tenant Postgres — there is **no** DB
  `UNIQUE`/constraint, because a global one would wrongly cap the table at one
  row across all tenants on a shared (SQLite) namespace.

This is an admin-presentation-and-policy feature, **not** a data-integrity
guarantee. An explicitly set [`policy`](#modeladminpolicy) takes precedence:
`singleton` only supplies the default create/delete behavior when no custom
policy owns those decisions.

---

## ModelAdmin methods

Override these on a `ModelAdmin` subclass to customize behavior beyond what the
declarative options express. All are `async`.

### Reference-label hooks

These turn raw id columns into human-readable text without an extra round-trip.

#### resolve_list_labels

`async resolve_list_labels(objs, *, session, ctx=None)`

Return `{column_name: {raw_value_str: label}}`. The list and detail endpoints
attach a `"<column>__label"` key to each serialized row so the UI shows the
label next to (not instead of) the raw id.

```python
class TenantMembershipRoleAdmin(ModelAdmin):
    async def resolve_list_labels(self, objs, *, session, ctx=None):
        role_ids = {o.role_id for o in objs}
        rows = (await session.execute(
            select(TenantRole.id, TenantRole.name).where(TenantRole.id.in_(role_ids))
        )).all()
        return {"role_id": {str(rid): name for rid, name in rows}}
```

Resolve in **batch** — one query per related table for the whole page
(`WHERE id IN (...)`), never one query per row. `session` is the same
request-scoped, tenant-aware session the rows came from, so cross-schema lookups
work through its `search_path`. Default: no labels.

#### resolve_fk_options

`async resolve_fk_options(field, *, session, ctx=None, q=None, limit=100)`

Return `[{"value": ..., "label": ...}]` to power a foreign-key dropdown the
generic resolver can't produce — a cross-schema target with no DB foreign key,
or a label that needs a join rather than a column. Return `None` for any field
you don't handle so the generic resolver takes over.

```python
class TenantMembershipRoleAdmin(ModelAdmin):
    async def resolve_fk_options(self, field, *, session, ctx=None, q=None, limit=100):
        if field != "membership_id":
            return None
        if ctx is None or ctx.tenant is None:
            return []  # no tenant context → offer nothing, never every tenant's members
        stmt = (
            select(TenantMembership.id, User.email)
            .join(User, User.id == TenantMembership.user_id)
            .where(TenantMembership.tenant_id == ctx.tenant.id)
        )
        if q and q.strip():
            stmt = stmt.where(User.email.ilike(f"%{q.strip()}%"))
        rows = (await session.execute(stmt.limit(limit))).all()
        return [{"value": str(mid), "label": email} for mid, email in rows]
```

Pair the override with `widgets = {"<field>": "foreign_key"}` so the column
renders as a dropdown even without a DB foreign key. Honor `q` (a label
substring) and `limit`, and resolve in one batched query.

### Lifecycle hooks

These fire for every mutation path (CRUD API, admin UI, bulk actions, import,
jobs) so app-side invariants don't depend on which router triggered the change.
They run only when a request context (`ctx`) is present; non-HTTP callers that
pass `ctx=None` (tests, scripts) skip every hook.

**Create order:** `before_validate` → schema clean → `validate_create` →
`before_create` → DB insert → `after_create`.

**Update order:** fetch row → `can_update_object` (policy) → schema + field
policy → `before_validate` → schema clean → `validate_update` → `before_update`
→ apply changes → `after_update`.

**Delete order:** fetch row → `before_delete` → `is_system` guard →
`session.delete` → `after_delete`.

| Hook | Signature | Use |
|---|---|---|
| `before_validate` | `(data, ctx) -> dict` | Tweak the raw payload before any validation. Fires for create and update. |
| `validate_create` | `(data, ctx) -> None` | Raise to reject a create (tenant-scoped uniqueness, cross-field rules). |
| `before_create` | `(data, ctx) -> dict` | Last chance to mutate the payload (server defaults, hashing, current tenant id). |
| `after_create` | `(obj, ctx) -> None` | Post-commit side effects (webhooks, search index, notifications). |
| `validate_update` | `(obj, data, ctx) -> None` | Raise to reject; state-dependent transitions ("only published posts can be archived"). |
| `before_update` | `(obj, data, ctx) -> dict` | Mutate the patch before it is applied. |
| `after_update` | `(obj, changes, ctx) -> None` | Post-commit; `changes` is the applied diff. |
| `before_delete` | `(obj, ctx) -> None` | Raise to refuse deletion ("cannot delete with active children"). |
| `after_delete` | `(obj, ctx) -> None` | Cleanup of external resources for the now-removed row. |

```python
class PostAdmin(ModelAdmin):
    async def before_create(self, data, ctx):
        data["author_id"] = ctx.principal.id
        return data

    async def validate_update(self, obj, data, ctx):
        if data.get("status") == "archived" and obj.status != "published":
            raise HTTPException(422, "Only published posts can be archived.")
```

### Computed properties

| Property | Returns |
|---|---|
| `model_name` | The resource key (`model.__tablename__`). |
| `display_label` / `display_label_plural` | The resolved singular / plural label. |
| `label_field` | The column used as this model's label in FK pickers (see [`display_field`](#modeladmindisplay_field)). |
| `all_protected` | The combined frozenset of protected field names (global registry + `protected_fields`). |

---

## InlineAdmin objects

An `InlineAdmin` describes a child model edited inline with its parent. Declare
it on the parent's [`inlines`](#modeladmininlines) list.

```python
from asterion.admin import InlineAdmin

class CommentInline(InlineAdmin):
    model = Comment
    fk_name = "post_id"
    fields = ["author", "body", "is_public"]
    readonly_fields = ["created_at"]
    extra = 1
    can_delete = True
    ordering = ["created_at"]

class PostAdmin(ModelAdmin):
    model = Post
    inlines = [CommentInline]
```

| Attribute | Type | Purpose |
|---|---|---|
| `model` | mapped class | **Required.** The child model. |
| `fk_name` | `str \| None` | Column on `model` pointing back at the parent. |
| `fields` | `list[str]` | Column order; `[]` means all writable non-protected columns. |
| `readonly_fields` | `list[str]` | Columns visible but not editable in the inline row. |
| `extra` | `int` | Blank rows the UI pre-renders for new entries. Default `0`. |
| `max_num` | `int \| None` | Hard cap on rows per parent. `None` = unlimited. |
| `can_delete` | `bool` | Whether the per-row delete control is shown. Default `True`. |
| `ordering` | `list[str]` | Sort order for existing rows. |
| `policy` | `AdminPolicy \| None` | Object-level gate enforced per child row. `None` inherits the parent's gate. |
| `widget` | `str \| None` | `"dual_list"` renders a transfer widget over `value_field` instead of the add-row table. `None` (default) keeps the table. |
| `value_field` | `str \| None` | The single assignment column for `widget="dual_list"`. `None` defaults to the first declared field. |

Inline writes happen in the **same transaction** as the parent: a failure on a
child rolls the parent write back (all-or-nothing). A child `policy` is
consulted independently — `can_create` for new rows, `can_update_object` for
edits, `can_delete_object` for removals.

### Dual-list (transfer) inlines

For M:N-style links — assigning permission keys or members to a role — an
add-row table is clumsy. Set `widget = "dual_list"` to render a Django-style
**available | assigned** transfer widget (with →/← and a per-side filter) over a
single `value_field`, and override `resolve_options` to supply the universe of
assignable values:

```python
class RolePermissionInline(InlineAdmin):
    model = TenantRolePermission
    fk_name = "role_id"
    fields = ["permission_key"]
    widget = "dual_list"
    value_field = "permission_key"

    async def resolve_options(self, *, session, ctx=None, q=None, limit=1000):
        return [{"value": k, "label": k} for k in await load_permission_keys(session)]
```

The widget fetches the options from `GET /{resource}/_inline_options/{inline}`
(authorized by `read` on the parent; the resolver scopes the values). Saving
**diffs** the assigned set against the existing rows — newly-assigned values are
created, removed ones are deleted — riding the same inline write path as the
table, so unchanged assignments are left untouched. With no `resolve_options`
the widget degrades to "remove only".

---

## AdminAction objects

An `AdminAction` is a named operation over one or more selected rows, exposed at
`POST /{resource}/_actions/{name}` (bulk) or
`POST /{resource}/{id}/_actions/{name}` (single row).

```python
from asterion.actions import AdminAction

class MarkPublished(AdminAction):
    name = "publish"
    label = "Mark as published"
    confirm = True

    async def run(self, objects, data, ctx):
        for obj in objects:
            obj.published = True
        # NOT commit — the router owns the transaction.
        return {"affected": len(objects), "summary": f"Published {len(objects)} post(s)."}

class PostAdmin(ModelAdmin):
    actions = [MarkPublished()]
```

The endpoint above is `POST /api/v1/admin/posts/_actions/publish` with body
`{"ids": [...], "data": {...}}`. The required permission is
`admin.posts.publish`.

| Attribute | Type | Purpose |
|---|---|---|
| `name` | `str` | **Required.** Action id; the suffix of the permission key. |
| `label` | `str` | Button text in the UI. |
| `confirm` | `bool` | UI hint to prompt before firing. Metadata only. Default `False`. |
| `bulk` | `bool` | Bulk (multi-row) vs single-row action. Default `True`. |
| `input_schema` | `type[BaseModel] \| None` | Pydantic model for extra inputs; the body's `data` is validated against it and passed to `run`. |

Implement **either** `run(objects, data, ctx)` (typed; preferred) **or** the
legacy `execute(records, session, user)`. The router dispatches to whichever you
override. An action defined with neither fails loudly rather than silently
no-opping.

`asterion.actions.BulkDeleteAction` ships ready to use (`name = "delete"`,
`confirm = True`); add it to `actions` to expose bulk delete with the implicit
`admin.<resource>.delete` permission.

---

## Fieldset objects

A `Fieldset` is one labeled group of fields for the admin form. Declare a list
of them on [`ModelAdmin.fieldsets`](#modeladminfieldsets).

```python
from asterion.admin import Fieldset

Fieldset("SEO", fields=["seo_title", "seo_description"], collapsed=True,
         description="Optional — overrides the auto-generated tags.")
```

| Argument | Type | Purpose |
|---|---|---|
| `label` | `str` | **Required.** Section header. |
| `fields` | `list[str]` | Ordered model attribute names in this section. |
| `collapsed` | `bool` | Render the section collapsed by default. Default `False`. |
| `description` | `str \| None` | One-liner shown under the header. |

`Fieldset` is a frozen dataclass. Fields not on the model, or filtered by
`protected_fields`, are dropped by the contract builder.

---

## AdminPolicy objects

An `AdminPolicy` layers object-level and field-level rules on top of the
permission-key checks. Both must allow for an operation to proceed: the
permission key gates the *route* by the caller's grant set; the policy gates the
*operation / individual field* by app-defined rules. Set it on
[`ModelAdmin.policy`](#modeladminpolicy).

Every method has a permissive default, so an admin without a policy behaves
exactly as if there were none. Override only what you want to constrain. All
methods are `async` (real checks often hit the DB).

```python
from asterion.admin import AdminPolicy, FieldPermission

class OwnerOnlyPolicy(AdminPolicy):
    async def can_update_object(self, obj, ctx):
        return obj.owner_id == ctx.principal.id

    async def field_permission(self, field, obj, ctx):
        if field == "internal_notes" and not ctx.is_superadmin:
            return FieldPermission.HIDDEN
        return FieldPermission.WRITE
```

| Method | Gate |
|---|---|
| `can_view_model(ctx)` | The whole admin (list + read + write). |
| `can_create(ctx)` | Create, before payload validation. |
| `can_view_object(obj, ctx)` | Read one row, after fetch. |
| `can_update_object(obj, ctx)` | Update, after fetch. |
| `can_delete_object(obj, ctx)` | Delete, after fetch. |
| `field_permission(field, obj, ctx)` | Per-field access (see below). `obj` is `None` on create. |

A denied gate returns `403`. The detail string is deliberately identical to a
permission-key denial so a client can't tell the two checks apart.

### FieldPermission

`field_permission` returns a `FieldPermission`:

* `WRITE` — full read + write (the default).
* `READ` — read only; the field is dropped from the create/update schema and a
  payload that sets it is rejected.
* `HIDDEN` — invisible everywhere (serialized output, contract, write schema).

The effective permission for a field is the **strictest** of its static class
(protected → `HIDDEN`, readonly / calculated → `READ`) and the policy's
per-caller decision — a policy can tighten but never loosen what
`protected_fields` / `readonly_fields` already locked down.

### Built-in policies

| Class | Effect |
|---|---|
| `ReadOnlyPolicy` | List + detail only. Create / update / delete return `False`; the contract reports no write capabilities. |
| `NoCreateDeletePolicy` | List + read + **update**; create and delete return `False`. For editable tables whose row lifecycle belongs to a dedicated path (e.g. accounts created via invite). |

Both surface their state in the contract (`read_only`, `disable_create`,
`disable_delete`) so the UI hides the matching controls, in addition to the
route-level `403`.

---

## Built-in admins

Several admins ship pre-registered when `enable_builtin_admins=True` (the
default). Each registers only if the model isn't already in the registry, so an
app can re-register its own variant and win.

**Tenant-local (RBAC) admins:**

| Class | Resource | Purpose |
|---|---|---|
| `TenantRoleAdmin` | `tenant_roles` | Manage tenant-local roles. |
| `TenantRolePermissionAdmin` | `tenant_role_permissions` | Manage role → permission-key assignments. |
| `TenantMembershipRoleAdmin` | `tenant_membership_roles` | Manage which membership has which role. |

**Global (public-schema) admins** — all `superadmin_only`, so a tenant-scoped
caller with an `admin.*` grant cannot reach them and read across tenants:

| Class | Resource | Write scope |
|---|---|---|
| `UserAdmin` | `users` | Update-only (no create/delete; accounts come from invite; password / TOTP hidden). |
| `TenantAdmin` | `tenants` | Update-only (no create/delete; `slug` / `schema_name` read-only). |
| `ImpersonationLogAdmin` | `impersonation_logs` | Read-only. |
| `AuditLogAdmin` | `audit_logs` | Read-only. |

Pass `enable_builtin_admins=False` on `CoreAdminConfig` to install none of them,
or `install_builtin_admins(..., include_global_admins=False)` to skip just the
global set.

---

## Registering ModelAdmin objects

Pass a `register` callback to `create_admin`. It receives the registry; call
`register(...)` for each admin. Your callback runs **after** the built-in admins
are installed, so re-registering a built-in resource overrides it.

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

Registration is keyed by the model's `__tablename__`; registering a second admin
for the same table replaces the first.

---

## The admin contract

Every option above is surfaced through the contract API so a client can render
forms, list columns, filters, and validate inputs without calling the CRUD
endpoints:

* `GET /api/v1/admin/_contract` — all registered resources (the sidebar +
  dashboard feed). In multi-tenant mode it lists only the resources reachable in
  the current request scope (tenant-scoped models need an active tenant; global
  models resolve only outside one).
* `GET /api/v1/admin/_contract/{resource}` — one resource's metadata.

The per-resource contract includes the field list (with widget, validation,
help text, conditions, dependencies, and per-caller field permission), the
fieldsets, filters, relations, inlines, the resolved `list_*` hints, the
`scope` / `show_in_nav` / `singleton` flags, and a `capabilities` block
(`create` / `update` / `delete` / `bulk_actions`) computed for the calling
principal. Protected and hidden fields are never emitted.
