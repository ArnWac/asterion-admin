# adminfoundry

FastAPI admin framework with built-in UI, JWT auth, RBAC, and optional multi-tenancy.

Register your SQLAlchemy models and get a full admin interface — list, detail, create, edit, delete, bulk actions, export, audit log, dashboard, dark mode, i18n, and more. No frontend tooling required.

## Install

```bash
# PostgreSQL
pip install adminfoundry[postgres]

# SQLite (development / testing)
pip install adminfoundry[sqlite]

# Optional extras
pip install adminfoundry[redis]   # Redis cache backend
pip install adminfoundry[s3]      # S3 storage backend
pip install adminfoundry[xlsx]    # Excel export
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
create_coreadmin(app, config=CoreAdminConfig(
    default_language="en",
    default_date_format="iso",
))
```

Create a superadmin and start the server:

```bash
adminfoundry create-superadmin
uvicorn myapp:app --reload
```

Open `http://localhost:8000/admin-ui`.

---

## Configuration

Non-secret framework config goes in `pyproject.toml`:

```toml
[tool.adminfoundry]
default_language      = "de"
default_date_format   = "eu"
default_show_timezone = true
enable_multi_tenant   = false
```

```python
config = CoreAdminConfig.from_pyproject()
create_coreadmin(app, config=config)
```

Secrets go in `.env`:

```env
DATABASE_URL=postgresql+asyncpg://user:pass@localhost/mydb
SECRET_KEY=your-random-secret-min-32-chars
```

---

## Bulk actions

```python
from adminfoundry.admin.actions import AdminAction

class PublishAction(AdminAction):
    name    = "publish"
    label   = "Publish selected"
    confirm = True
    danger  = False
    bulk    = True

    async def execute(self, objects, db, user):
        for obj in objects:
            obj.published = True
        await db.commit()
        return {"summary": f"{len(objects)} published"}

class ArticleAdmin(ModelAdmin):
    model   = Article
    actions = [PublishAction()]
```

---

## Export

List views include CSV, JSON, and Excel export (requires `adminfoundry[xlsx]`). Datetimes are converted to the tenant's configured timezone automatically.

---

## Dashboard widgets

The dashboard adapts to the current user — superadmins see global metrics, tenant users see their own record counts.

Add custom widgets via `CoreAdminConfig`:

```python
from adminfoundry import DashboardWidget, CoreAdminConfig

class RevenueWidget(DashboardWidget):
    id    = "revenue"
    title = "Revenue"

    async def get_data(self, user, db, request):
        total = await db.scalar(select(func.sum(Order.amount)))
        return {"stats": [{"label": "Total", "value": f"€{total or 0:,.0f}"}]}

create_coreadmin(app, config=CoreAdminConfig(
    dashboard_widgets=[RevenueWidget()],  # replaces built-in defaults
))
```

Built-in widgets: `ModelCountsWidget` (record counts, tenant-scoped), `AdminMetricsWidget` (request/action counters, superadmin only).

---

## Signals

```python
from adminfoundry import signals

@signals.on("post_create")
async def on_create(model_name, object_id, user, **kw):
    print(f"{user.email} created {model_name} {object_id}")

# Available: post_create, post_update, pre_delete, post_delete
```

---

## Cache

```python
from adminfoundry import cache

await cache.set("key", value, ttl=300)
value = await cache.get("key")
```

Configure backend in `CoreAdminConfig`:

```python
CoreAdminConfig(cache_backend="redis://localhost:6379/0")  # default: in-process memory
```

---

## Storage

```python
from adminfoundry.storage import storage, generate_path

path = generate_path("uploads", "avatar.png")
url  = await storage.save(path, file_bytes)
```

Configure backend:

```python
CoreAdminConfig(storage_backend=S3Storage(bucket="my-bucket"))  # default: LocalStorage("uploads")
```

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

---

## Fixtures

```python
from adminfoundry.fixtures import load_fixture, dump_fixture

# Load from JSON or YAML
await load_fixture("fixtures/initial.json", session)

# Dump to file
await dump_fixture("post", session, "fixtures/posts.json")
```

---

## Pluggable auth

```python
from adminfoundry import AuthProvider, CoreAdminConfig, create_coreadmin

class MyAuthProvider(AuthProvider):
    async def authenticate(self, request, token, db):
        user = await my_system.verify(token)
        if not user:
            raise HTTPException(401)
        return user

    def is_superadmin(self, user) -> bool:
        return user.is_staff

create_coreadmin(app, config=CoreAdminConfig(
    auth_provider=MyAuthProvider(),
    include_auth_routes=False,
))
```

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

Tenant language and timezone are set in the admin UI and applied to all exports and UI formatting for that tenant.

---

## Locale hierarchy

```
User preference (Settings page) → Tenant settings (DB) → CoreAdminConfig default
```

---

## Key environment variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://...` | SQLAlchemy async DB URL |
| `SECRET_KEY` | — | JWT signing key — **required in production** |
| `MULTI_TENANT` | `false` | Enable multi-tenancy |
| `TENANT_RESOLUTION_STRATEGY` | `header` | `header` or `subdomain` |
| `ENABLE_BUILTIN_ADMIN_UI` | `true` | Mount the built-in admin panel |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | Access token lifetime |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh token lifetime |

---

## CLI

```bash
adminfoundry create-superadmin      # create the first superadmin
adminfoundry doctor                 # check DB connection, registry, config
adminfoundry inspect-registry       # list registered models
adminfoundry migrate generate <msg> # generate an Alembic migration
adminfoundry migrate apply          # apply pending migrations
adminfoundry migrate status         # show current migration state
adminfoundry loaddata fixtures.json # load fixture data
adminfoundry dumpdata <model>       # dump model data to JSON
```

Plugin commands are registered via entry points:

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

- [`examples/blog/`](examples/blog/app.py) — minimal single-tenant app with SQLite, bulk actions, and custom dashboard

## Requirements

- Python 3.11+
- FastAPI 0.111+
- SQLAlchemy 2.0+

## License

MIT
