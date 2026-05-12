# adminfoundry

FastAPI admin framework with built-in UI, JWT auth, RBAC, and optional multi-tenancy.

Register your SQLAlchemy models and get a full admin interface — list, detail, create, edit, delete, bulk actions, import, export, audit log, approval workflows, dashboard widgets, dark mode, i18n, and more. No frontend tooling required.

## Install

```bash
# PostgreSQL
pip install adminfoundry[postgres]

# SQLite (development / testing)
pip install adminfoundry[sqlite]

# Optional extras
pip install adminfoundry[redis]   # Redis cache + rate limiting backend
pip install adminfoundry[s3]      # S3 storage backend
pip install adminfoundry[xlsx]    # Excel (.xlsx) export
pip install adminfoundry[yaml]    # YAML config support
```

## Quickstart

```python
from fastapi import FastAPI
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String

from adminfoundry import create_coreadmin, CoreAdminConfig, ModelAdmin, admin_site
from adminfoundry.models.base import TimestampedBase

class Article(TimestampedBase):
    __tablename__ = "articles"
    title:     Mapped[str]  = mapped_column(String(255))
    published: Mapped[bool] = mapped_column(default=False)

class ArticleAdmin(ModelAdmin):
    model           = Article
    list_display    = ["title", "published", "created_at"]
    search_fields   = ["title"]
    filter_fields   = ["published"]
    readonly_fields = ["id", "created_at", "updated_at"]

admin_site.register(ArticleAdmin())

app = FastAPI()
create_coreadmin(app, config=CoreAdminConfig())
```

```bash
adminfoundry create-superadmin
uvicorn myapp:app --reload
```

Open `http://localhost:8000/admin-ui`.

---

## Configuration

### `CoreAdminConfig` — all options

```python
from adminfoundry import CoreAdminConfig

config = CoreAdminConfig(
    # --- Core ---
    enable_builtin_ui   = True,   # mount the built-in admin panel
    enable_multi_tenant = False,  # enable multi-tenancy
    enable_basic_audit  = True,   # write an AuditLog entry after every mutating request
    enable_workflows    = False,  # approval workflows for admin changes

    # --- Locale defaults (users can override in Settings page) ---
    default_language    = "en",          # BCP 47: "en", "de", "fr", "es", "pt"
    default_date_format = "locale",      # "locale" | "iso" | "eu" | "us" | "custom"
    default_date_pattern = "%Y-%m-%d %H:%M",  # used when date_format = "custom"
    default_show_timezone = False,

    # --- Extensions ---
    extensions = [],          # list of ExtensionBase instances

    # --- Auth ---
    auth_provider      = None,  # custom AuthProvider subclass; None = built-in JWT
    include_auth_routes = True, # mount /api/v1/auth/login, /logout, /refresh, /me

    # --- Cache ---
    cache_backend = None,  # None = in-process memory; "redis://localhost:6379/0"

    # --- Storage ---
    storage_backend = None,  # None = LocalStorage("uploads"); or S3Storage(...)

    # --- Dashboard ---
    dashboard_widgets = [],  # replaces built-in defaults when non-empty
)
```

Load from `pyproject.toml`:

```toml
[tool.adminfoundry]
default_language      = "de"
default_date_format   = "eu"
default_show_timezone = true
enable_multi_tenant   = false
enable_workflows      = false
```

```python
config = CoreAdminConfig.from_pyproject()
create_coreadmin(app, config=config)
```

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://...` | SQLAlchemy async DB URL |
| `SECRET_KEY` | — | JWT signing key — **required in production** |
| `ALGORITHM` | `HS256` | JWT algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | Access token lifetime |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh token lifetime |
| `CORS_ORIGINS` | `["http://localhost:3000"]` | Allowed CORS origins |
| `REDIS_URL` | `None` | Redis URL (used for cache + rate limiting) |
| `MULTI_TENANT` | `false` | Enable multi-tenancy |
| `TENANT_RESOLUTION_STRATEGY` | `header` | `header` (X-Tenant-Slug) or `subdomain` |
| `ENABLE_BUILTIN_ADMIN_UI` | `true` | Mount the built-in panel |
| `ADMIN_UI_PATH` | `/admin-ui` | URL prefix for the built-in panel |
| `ADMIN_TITLE` | `adminfoundry` | Browser title and branding |
| `ENABLE_WORKFLOWS` | `false` | Approval workflow feature |
| `AUDIT_LOG_RETENTION_DAYS` | `90` | Days to keep audit log entries (0 = forever) |
| `PASSWORD_MIN_LENGTH` | `8` | Minimum password length |
| `PASSWORD_RESET_ENABLED` | `true` | Enable password reset via email |
| `PASSWORD_RESET_TIMEOUT_MINUTES` | `30` | Reset link expiry |
| `ENFORCE_2FA_FOR_SUPERADMIN` | `false` | Require TOTP for superadmin login |
| `TOTP_ISSUER` | `adminfoundry` | Issuer label in authenticator apps |
| `LOGIN_MAX_FAILURES` | `5` | Failed logins before account lockout |
| `LOGIN_LOCKOUT_MINUTES` | `15` | Lockout duration |
| `STEP_UP_WINDOW_MINUTES` | `15` | Minutes since login required for protected actions |
| `EMAIL_HOST` / `EMAIL_PORT` | — | SMTP server for password reset emails |
| `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` | — | SMTP credentials |
| `EMAIL_DEFAULT_FROM` | `noreply@example.com` | From address for system emails |
| `LOG_JSON` | `true` | Structured JSON logging |
| `DEBUG` | `false` | Debug mode |

---

## `ModelAdmin` — full reference

```python
from adminfoundry import ModelAdmin

class ArticleAdmin(ModelAdmin):
    model = Article

    # --- List view ---
    list_display    = ["title", "published", "created_at"]  # columns shown in list
    list_editable   = ["published"]   # fields editable inline in the list
    search_fields   = ["title"]       # full-text search across these fields
    filter_fields   = ["published"]   # sidebar filter dropdowns
    range_filter_fields = ["created_at"]   # ?created_at__gte / __lte filters
    enum_filter_fields  = ["status"]       # ?status__in=a,b,c filters
    ordering        = ["-created_at"] # default sort (prefix - for DESC)

    # --- Fields ---
    readonly_fields  = ["id", "created_at", "updated_at"]  # excluded from create/update
    protected_fields = ["internal_key"]   # hidden from all API responses
    extra_create_fields = {"set_password": str}  # virtual fields only on create form

    # --- Labels ---
    label        = "Article"
    label_plural = "Articles"
    description  = "Published blog posts"

    # --- Actions ---
    actions = [PublishAction(), BulkDeleteAction()]

    # --- Relations ---
    inline_fields   = ["comments"]   # SQLAlchemy relationship attrs editable inline
    lookup_field    = "title"        # field used as label in FK select widgets
    field_choices_urls = {           # field gets a <select> populated from this URL
        "category_id": "/api/v1/admin/categories/lookup",
    }

    # --- Create flow ---
    create_redirect = "detail"  # redirect to detail after create; default: "list"

    # --- Multi-tenancy ---
    tenant_scoped            = False  # filter by tenant_id in tenant context
    global_only_in_root_panel = False # superadmin root panel shows WHERE tenant_id IS NULL

    # --- Auth ---
    admin_only   = True   # False = open to role-based access
    access_roles = []     # roles that grant base CRUD when admin_only=False

    # --- Fine-grained RBAC ---
    field_policies = {
        "salary": {"view_roles": ["hr"], "edit_roles": ["hr_manager"]},
        "notes":  {"view_roles": None, "edit_roles": []},  # view all, edit superadmin only
    }
    action_policies = {
        "publish": {"roles": ["editor"]},  # only editors can trigger this action
    }

    # --- Row-level access ---
    # record_filter: callable(user) -> SQLAlchemy WHERE clause
    # scopes all list queries for non-superadmin users
    record_filter = lambda user: Article.author_id == user.id

    # record_access: callable(user, record) -> bool
    # controls detail/update/delete for individual records
    record_access = lambda user, record: record.author_id == user.id

    # --- Approval workflow (requires enable_workflows=True) ---
    requires_approval = False  # True: all writes create a ChangeRequest first

    # --- Deletion ---
    allow_delete = True  # False: block deletion at API level (e.g. immutable audit logs)

    # --- Permission matrix (role-like models) ---
    permission_matrix = False  # True: show CRUD permission matrix in detail view

    # --- Hooks ---
    @classmethod
    def before_create(cls, data: dict) -> dict:
        """Transform create data before the model instance is built."""
        data["slug"] = data["title"].lower().replace(" ", "-")
        return data

    def field_permission(self, user, field_name, record):
        """Per-record field policy — override for state-dependent visibility."""
        if field_name == "internal_notes" and record.status != "review":
            from adminfoundry.authz.rules import FieldPolicy
            return FieldPolicy(can_view=False, can_edit=False)
        return None
```

---

## Actions

```python
from adminfoundry.admin.actions import AdminAction

class PublishAction(AdminAction):
    name    = "publish"
    label   = "Publish selected"
    danger  = False
    confirm = True   # show confirmation dialog before executing
    bulk    = True   # shown in bulk action dropdown (requires row selection)
    single  = True   # shown in single-record action menu

    async def execute(self, objects, db, user):
        for obj in objects:
            obj.published = True
        await db.commit()
        return {"summary": f"{len(objects)} articles published", "affected": len(objects)}
```

Actions are exposed via `POST /api/v1/admin/{model}/bulk-action`.

---

## Import / Export

**Export** is built into every list view — CSV, JSON, and Excel (requires `adminfoundry[xlsx]`). Datetimes are converted to the tenant's configured timezone automatically.

**Import** accepts JSON rows and supports dry-run validation before committing:

```bash
# Dry run — validate only, no writes
POST /api/v1/jobs/admin/articles/import
{"rows": [{"title": "Hello", "published": false}], "dry_run": true}

# Commit
POST /api/v1/jobs/admin/articles/import
{"rows": [...], "dry_run": false, "idempotency_key": "import-2024-01-batch-1"}
```

Both respect field-level permissions and protected fields. Import supports idempotency keys to safely retry failed uploads.

---

## Approval workflows

When `enable_workflows=True`, models with `requires_approval = True` route all writes through a review step:

```python
class PayoutAdmin(ModelAdmin):
    model             = Payout
    requires_approval = True
```

```bash
# Submit a proposed change (does not apply immediately)
POST /api/v1/workflow/change-requests
{"model_name": "payouts", "operation": "update", "object_id": "...", "proposed_data": {...}}

# Superadmin review
POST /api/v1/workflow/change-requests/{id}/review
{"action": "approve", "reason": "Checked against ledger"}

# Undo an approved change
POST /api/v1/workflow/change-requests/{id}/revert
{"reason": "Duplicate entry"}

# List all pending requests
GET /api/v1/workflow/change-requests
```

---

## Dashboard widgets

The dashboard adapts to context — superadmins see global metrics, tenant users see their own record counts.

**Built-in widgets:**
- `ModelCountsWidget` — record counts per registered model, tenant-scoped
- `AdminMetricsWidget` — request/action counters, superadmin only

**Custom widgets:**

```python
from adminfoundry.dashboard import DashboardWidget
from sqlalchemy import select, func

class RevenueWidget(DashboardWidget):
    id    = "revenue"
    title = "Revenue"
    superadmin_only = False  # True = hidden from non-superadmin users

    def widget_type(self) -> str:
        return "stats"  # "stats" | "counts"

    async def get_data(self, user, db, request):
        total = await db.scalar(select(func.sum(Order.amount)))
        return {
            "stats": [
                {"label": "Total revenue", "value": f"€{total or 0:,.2f}"},
                {"label": "This month",    "value": f"€{monthly or 0:,.2f}", "sub": "+12%"},
            ]
        }

config = CoreAdminConfig(
    dashboard_widgets=[RevenueWidget()],  # replaces built-in defaults
)
```

Widget data shapes:
- `stats` → `{"stats": [{"label": str, "value": str|int, "sub": str?}]}`
- `counts` → `{"rows": [{"model": str, "label": str, "count": int}]}`

---

## Pluggable auth

Replace the built-in JWT authentication to integrate an existing user system:

```python
from adminfoundry import AuthProvider, CoreAdminConfig, create_coreadmin

class MyAuthProvider(AuthProvider):
    async def authenticate(self, request, token, db):
        """Return the user object or raise HTTP 401."""
        user = await my_system.verify_token(token)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid token")
        request.state.token_payload = {"sub": str(user.id)}
        return user

    def is_superadmin(self, user) -> bool:
        return user.is_staff

    def get_role_names(self, user) -> list[str]:
        return [g.name for g in user.groups]

create_coreadmin(app, config=CoreAdminConfig(
    auth_provider       = MyAuthProvider(),
    include_auth_routes = False,  # skip built-in login/logout/refresh routes
))
```

The `authenticate` method receives the raw Bearer token and must return the user object. `is_superadmin` and `get_role_names` bridge your user model to the adminfoundry permission system.

---

## RBAC — role-based access control

Roles are assigned to users through the admin panel. Permission groups (`roles` table) store per-model CRUD grants configured via the permission matrix UI.

Non-superadmin access to a model:

```python
class ReportAdmin(ModelAdmin):
    model        = Report
    admin_only   = False          # open to role-based access
    access_roles = ["analyst"]    # any user with this role gets base CRUD

    field_policies = {
        "raw_data": {"view_roles": ["data_engineer"], "edit_roles": []},
    }
    action_policies = {
        "export_full": {"roles": ["data_engineer"]},
    }
```

Row-level scoping:

```python
class TicketAdmin(ModelAdmin):
    model         = Ticket
    admin_only    = False
    access_roles  = ["support"]
    record_filter = lambda user: Ticket.assigned_to == user.id
    record_access = lambda user, record: record.assigned_to == user.id
```

---

## Impersonation

Superadmins can enter a tenant panel as any user via the admin UI, or programmatically:

```bash
# Start impersonation — returns a short-lived impersonation token
POST /api/v1/tenants/{tenant_id}/impersonate
{"target_user_id": "uuid"}   # omit to impersonate as yourself

# Revoke an active impersonation token
POST /api/v1/tenants/{tenant_id}/impersonate/revoke
{"jti": "token-jti"}
```

Impersonation tokens cannot be refreshed and are blocked on write-only superadmin routes.

---

## Multi-tenancy

```python
create_coreadmin(app, config=CoreAdminConfig(enable_multi_tenant=True))
```

Tenant resolution via `X-Tenant-Slug` header (default) or subdomain (`TENANT_RESOLUTION_STRATEGY=subdomain`).

Mark models as tenant-scoped:

```python
class ProjectAdmin(ModelAdmin):
    model         = Project
    tenant_scoped = True
```

Tenant-scoped models are filtered by `tenant_id` automatically. Superadmins see all tenants in the root panel; within a tenant panel only that tenant's data is visible.

Tenant language, timezone, and date format are set in the admin UI and applied to all exports and UI formatting for that tenant.

**Locale hierarchy:**
```
User preference (Settings page) → Tenant settings (DB) → CoreAdminConfig default
```

---

## Audit log

Every mutating request writes an `AuditLog` entry automatically (when `enable_basic_audit=True`). Entries include: actor email, action, object ID, changed fields, IP address, tenant context.

Retention is configurable (`AUDIT_LOG_RETENTION_DAYS`, default 90 days). Set to `0` to keep forever.

View audit history for a specific object:

```bash
GET /api/v1/audit?object_id={uuid}
```

---

## Webhooks

Register HTTP endpoints to receive signal events — configured in code, no UI or database table required.

```bash
pip install adminfoundry[webhooks]   # adds httpx
```

```python
from adminfoundry import webhooks

webhooks.register(
    url="https://my-service.com/hooks/adminfoundry",
    events=["post_create", "post_update", "post_delete"],
    secret="my-hmac-secret",      # optional but recommended
    model_filter=["articles"],    # optional — omit to receive all models
)
```

Every matching event fires a `POST` to the URL with a JSON payload:

```json
{
    "event":      "post_create",
    "timestamp":  1718000000,
    "model_name": "articles",
    "object_id":  "uuid",
    "actor":      "admin@example.com",
    "changes":    null
}
```

When `secret` is set, each request includes an `X-Signature-256: sha256=<hmac>` header. Verify on the receiving end:

```python
import hashlib, hmac

body = await request.body()
expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
assert hmac.compare_digest(expected, request.headers["X-Signature-256"])
```

Available events: `post_create`, `post_update`, `pre_delete`, `post_delete`, `post_login`, `post_logout`, `post_password_change`.

---

## Signals

```python
from adminfoundry import signals

@signals.on("post_create")
async def on_article_created(model_name, obj, user, **kw):
    await notify_subscribers(obj.id)

@signals.on("pre_delete")
async def guard_delete(model_name, obj, user, **kw):
    if obj.is_locked:
        raise ValueError("Cannot delete locked records")
```

Available signals: `post_create`, `post_update`, `pre_delete`, `post_delete`.

---

## Cache

```python
from adminfoundry import cache

await cache.set("my_key", value, ttl=300)
value = await cache.get("my_key")
await cache.delete("my_key")
```

Backend:

```python
CoreAdminConfig(cache_backend="redis://localhost:6379/0")  # default: in-process memory
```

Requires `adminfoundry[redis]` for the Redis backend.

---

## Storage

```python
from adminfoundry.storage import storage, generate_path

path = generate_path("avatars", "user-123.png")
url  = await storage.save(path, file_bytes)
data = await storage.load(path)
await storage.delete(path)
```

Backend:

```python
from adminfoundry.storage import S3Storage

CoreAdminConfig(storage_backend=S3Storage(bucket="my-bucket", region="eu-central-1"))
# default: LocalStorage("uploads")
```

Requires `adminfoundry[s3]` for the S3 backend.

---

## i18n

```python
from adminfoundry import t

msg = t("welcome", lang="de", name="Arne")
```

Built-in catalogs: `en`, `de`, `fr`, `es`, `pt`. Add your own:

```python
from adminfoundry.i18n import add_catalog
add_catalog("nl", {"welcome": "Welkom, {name}!"})
```

The admin UI language follows the locale hierarchy (user → tenant → config default).

---

## Jobs

Every import, export, and bulk action is tracked as a `Job` record with status, progress, result summary, and idempotency support:

```bash
GET  /api/v1/jobs           # list all jobs (superadmin)
GET  /api/v1/jobs/{job_id}  # get one job (initiator or superadmin)
```

Idempotency keys prevent duplicate imports on retry:

```json
{"rows": [...], "dry_run": false, "idempotency_key": "batch-2024-01-01"}
```

---

## Observability

In-process counters are collected automatically and surfaced on the dashboard (AdminMetricsWidget) and health endpoint:

```bash
GET /api/v1/health
```

Counters include: request count, error rate, action count, audit write failures, contract version usage, client type breakdown.

Replace with Prometheus/OpenTelemetry by calling the counter functions from `adminfoundry.extensions.observability.admin_metrics` in your instrumentation layer.

---

## CLI

```bash
adminfoundry create-superadmin      # create the first superadmin
adminfoundry doctor                 # check DB connection, registry, config
adminfoundry inspect-registry       # list registered models and their config
adminfoundry migrate generate <msg> # generate an Alembic migration
adminfoundry migrate apply          # apply pending migrations
adminfoundry migrate status         # show current migration state
```

Plugin commands via entry points:

```toml
[project.entry-points."adminfoundry.commands"]
my_cmd = "myapp.cli:my_command_fn"
```

---

## Testing

```bash
pip install -e ".[dev]"
pytest tests/ -q
```

---

## Examples

Two runnable example apps are included. Both use SQLite — no database setup needed.

### Blog — single-tenant

```bash
uvicorn examples.blog.app:app --reload --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000/admin-ui` — Login: `admin@example.com` / `admin123`

Source: [`examples/blog/app.py`](examples/blog/app.py)

### SaaS — multi-tenant with subdomain routing

```bash
uvicorn examples.saas.app:app --reload --host 0.0.0.0 --port 8000
```

| URL | Context |
|---|---|
| `http://localhost:8000/admin-ui` | Superadmin panel (global view) |
| `http://acme.localhost:8000/admin-ui` | Tenant *acme* |
| `http://globex.localhost:8000/admin-ui` | Tenant *globex* |

Login: `admin@example.com` / `admin123`

`*.localhost` resolves to `127.0.0.1` on modern OSes — no `/etc/hosts` entry needed.

> **SQLite note:** delete `saas.db` / `blog.db` after pulling schema changes (SQLite has no migrations — `create_all` recreates the file on next start).

Source: [`examples/saas/app.py`](examples/saas/app.py)

---

## Requirements

- Python 3.11+
- FastAPI 0.111+
- SQLAlchemy 2.0+

## License

MIT
