# adminfoundry

A batteries-included admin framework for FastAPI. Drop it into your app, register your SQLAlchemy models, and get a full admin panel with authentication, RBAC, multi-tenancy, audit logging, and a built-in UI — without writing boilerplate.

## Features

- **Admin UI** — built-in HTML panel, no frontend tooling required
- **Auth** — JWT login, refresh, logout, token blacklist
- **RBAC** — role-based access control with field-level policies
- **Multi-tenancy** — subdomain or header-based tenant resolution, schema-isolated on PostgreSQL
- **Audit log** — every admin action is logged automatically
- **Impersonation** — superadmin can act as any user, with revocation
- **Workflow approvals** — optional change-request flow before mutations are applied
- **CLI** — `adminfoundry init`, `create-superadmin`, `doctor`, `db upgrade`

---

## Quick start

### 1. Install

```bash
pip install adminfoundry[dev]
```

Or from source:

```bash
git clone <repo>
cd adminfoundry
pip install -e ".[dev]"
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env — set DATABASE_URL and SECRET_KEY at minimum
```

### 3. Start PostgreSQL

```bash
docker compose up -d
```

### 4. Run migrations

```bash
make migrate-shared
```

### 5. Create a superadmin

```bash
adminfoundry create-superadmin
```

### 6. Start the server

```bash
uvicorn adminfoundry.main:app --reload
```

Admin UI: `http://localhost:8000/admin-ui`
API docs: `http://localhost:8000/docs`

---

## Register your first model

```python
from adminfoundry.admin import admin_site
from adminfoundry.admin.model_admin import ModelAdmin
from myapp.models import Article

class ArticleAdmin(ModelAdmin):
    model         = Article
    label         = "Article"
    label_plural  = "Articles"
    list_display  = ["title", "published", "created_at"]
    search_fields = ["title"]
    filter_fields = ["published"]
    readonly_fields = ["id", "created_at", "updated_at"]

admin_site.register(ArticleAdmin())
```

See [examples/blog/app.py](examples/blog/app.py) for a complete single-tenant example.

---

## Multi-tenant setup

Set `MULTI_TENANT=true` and `TENANT_RESOLUTION_STRATEGY=subdomain` in `.env`.

```
admin.yourdomain.com     →  no tenant header  →  superadmin panel (Users, Roles, Tenants)
acme.yourdomain.com      →  tenant: acme      →  acme-scoped models only
globex.yourdomain.com    →  tenant: globex    →  globex-scoped models only
```

Mark models as tenant-scoped:

```python
class ProjectAdmin(ModelAdmin):
    model         = Project
    tenant_scoped = True   # only visible when a tenant context is active
```

See [examples/saas/app.py](examples/saas/app.py) for a complete multi-tenant example including an nginx config snippet.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://...` | SQLAlchemy async DB URL |
| `SECRET_KEY` | — | JWT signing key — **must be set in production** |
| `MULTI_TENANT` | `false` | Enable multi-tenancy |
| `TENANT_RESOLUTION_STRATEGY` | `header` | `header` or `subdomain` |
| `ENABLE_BUILTIN_ADMIN_UI` | `true` | Mount the built-in HTML admin panel |
| `ENABLE_WORKFLOWS` | `true` | Enable change-request approval flow |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | Access token lifetime |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh token lifetime |

---

## CLI

```bash
adminfoundry init                  # scaffold a new app
adminfoundry create-superadmin     # create the first superadmin user
adminfoundry doctor                # check DB, registry, extensions
adminfoundry inspect-registry      # list registered models and their config
adminfoundry db upgrade            # run shared Alembic migrations
adminfoundry tenant migrate <slug> # create PostgreSQL schema for a tenant
```

---

## Testing

```bash
make test
# or
pytest tests/ -q
```

---

## Project layout

```
adminfoundry/
  admin/          contract, capabilities, router, registry, schema builder
  authz/          policy engine, field/record-level rules
  core/           typed config (CoreAdminConfig)
  middleware/      audit, tenant, rate limit, security headers, logging
  models/         User, Role, Tenant, AuditLog, ImpersonationLog, ChangeRequest
  routers/        auth, users, roles, tenants, audit, workflow, admin UI
  schemas/        Pydantic contracts for all endpoints
  services/       session security, workflow orchestration
  observability/  in-process metrics counters
  extensions/     jobs, import/export, workflows (optional)
examples/
  blog/           minimal single-tenant app
  saas/           multi-tenant app with subdomain routing
migrations/
  shared/         Alembic env for the shared schema
  tenant/         Alembic env for per-tenant schemas
```
