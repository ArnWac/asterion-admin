# adminfoundry

> **Alpha / experimental.** API and features may change without notice.

A contract-driven FastAPI admin framework with built-in security and first-class multi-tenancy.

Register your SQLAlchemy models declaratively and get a full admin interface — CRUD, bulk actions, audit log, role-based access, optional multi-tenant isolation, a built-in lightweight UI, and a renderer-independent contract API.

---

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
```

---

## Quickstart (single-tenant)

```python
from fastapi import FastAPI
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String

from adminfoundry import create_admin, CoreAdminConfig, ModelAdmin, admin_site
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
create_admin(app, config=CoreAdminConfig())
```

See [`examples/basic_single_tenant/`](examples/basic_single_tenant/) for a runnable version.

---

## Multi-tenant

```python
from adminfoundry.middleware.tenant import TenantMiddleware

app = FastAPI()
app.add_middleware(TenantMiddleware)   # resolves tenant from subdomain or X-Tenant-Slug header

create_admin(app, config=CoreAdminConfig(enable_multi_tenant=True))
```

See [`examples/basic_multi_tenant/`](examples/basic_multi_tenant/) for a runnable version.

---

## Feature Status

| Feature | Status |
|---------|--------|
| CRUD routes (list, detail, create, update, delete) | ✅ Implemented |
| Bulk actions | ✅ Implemented |
| Renderer-independent contract API | ✅ Implemented |
| Built-in lightweight admin UI | ✅ Implemented |
| JWT authentication | ✅ Implemented |
| Role-based access control | ✅ Implemented |
| Per-model field policies | ✅ Implemented |
| Multi-tenancy (subdomain / header) | ✅ Implemented |
| Audit log | ✅ Implemented |
| CSV import | ✅ Implemented |
| Computed / virtual fields | ✅ Implemented |
| Soft delete | ✅ Implemented |
| Dashboard widgets | ✅ Implemented |
| Signals & webhooks | ✅ Implemented |
| i18n / locale support | ✅ Implemented |
| Approval workflow | ⚠️ Backend only (UI pending) |
| Background jobs | ⚠️ Extension (opt-in) |
| SCIM / SAML | ❌ Not implemented |
| Flutter UI | ❌ Not implemented |

---

## Examples

| Example | Description |
|---------|-------------|
| [`examples/basic_single_tenant/`](examples/basic_single_tenant/) | Minimal single-tenant integration |
| [`examples/basic_multi_tenant/`](examples/basic_multi_tenant/) | Minimal multi-tenant integration |
| [`examples/default/`](examples/default/) | Full-featured reference (all middleware + extensions) |
| [`examples/blog/`](examples/blog/) | Blog with computed fields, publish action |
| [`examples/saas/`](examples/saas/) | SaaS multi-tenant with demo tenants |

---

## Docs

- [Architecture](docs/architecture.md)
- [Tenancy](docs/tenancy.md)
- [Security](docs/security.md)
- [ModelAdmin configuration](docs/model-admin.md)
- [Protected fields](docs/protected-fields.md)

---

## CLI

```bash
adminfoundry create-superadmin   # interactive superadmin creation
adminfoundry inspect-registry    # list registered models + fields
adminfoundry doctor              # check DB connectivity and config
adminfoundry init-roles          # seed default admin/user roles
```

---

## Environment

Key variables (see `.env.example`):

```
DATABASE_URL=postgresql+asyncpg://...
SECRET_KEY=...
MULTI_TENANT=false
TENANT_RESOLUTION_STRATEGY=header   # or: subdomain
ADMIN_UI_PATH=/admin-ui
```
