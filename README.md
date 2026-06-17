# adminfoundry

[![CI](https://github.com/ArnWac/adminfoundry/actions/workflows/ci.yml/badge.svg)](https://github.com/ArnWac/adminfoundry/actions/workflows/ci.yml)

A contract-driven FastAPI admin framework for SQLAlchemy applications.

Register your models declaratively and get a generated admin: CRUD routes,
permission-key authorization, multi-tenant scoping (PostgreSQL schema-per-tenant),
audit log, impersonation, a small built-in UI shell, and a JSON contract API
that any frontend can consume.

---

## Install

```bash
# PostgreSQL deployment
pip install adminfoundry[postgres]

# SQLite (development / testing)
pip install adminfoundry[sqlite]
```

Requires Python 3.11+.

---

## Quickstart

```python
# app.py
from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from adminfoundry import CoreAdminConfig, ModelAdmin, create_admin
from adminfoundry.actions import BulkDeleteAction
from adminfoundry.models.base import GlobalModel


class Post(GlobalModel):
    __tablename__ = "posts"
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class PostAdmin(ModelAdmin):
    model = Post
    list_display = ["id", "title", "published", "created_at"]
    search_fields = ["title", "body"]
    ordering = ["-created_at"]
    readonly_fields = ["id", "created_at", "updated_at"]
    actions = [BulkDeleteAction()]


def register(registry):
    registry.register(PostAdmin)


app = create_admin(
    config=CoreAdminConfig.from_env(),
    register=register,
)
```

```bash
export ADMINFOUNDRY_DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/myapp"
export ADMINFOUNDRY_SECRET_KEY="$(openssl rand -hex 32)"

adminfoundry db upgrade-public
adminfoundry permissions sync --app app:app
adminfoundry create-superadmin --email admin@example.com

uvicorn app:app --reload
```

Then visit `http://127.0.0.1:8000/admin/login` and sign in.

---

## What you get

| Route prefix | Purpose |
|---|---|
| `/api/v1/auth/{login,me}` | JWT auth + logout-all (token_version revocation) |
| `/api/v1/admin/_contract` | Per-resource metadata so a UI can render forms generically |
| `/api/v1/admin/{resource}` | CRUD for tenant-local resources, permission-key gated |
| `/api/v1/admin/{resource}/_actions/{action}` | Run a declared admin action over a list of records |
| `/api/v1/root/impersonate` | Superadmin starts an impersonation session |
| `/api/v1/root/{users,tenants}` | Superadmin-only global views |
| `/admin/...` | Minimal SPA shell (login + dashboard + per-resource pages) |
| `/healthz`, `/readyz` | Liveness + readiness probes |

Every API error follows one envelope:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Payload contains non-writable fields.",
    "fields": [{"name": "password", "message": "Invalid field."}],
    "request_id": "8e1f..."
  }
}
```

Every response carries `X-Request-ID` (echoed from the inbound header or
generated). Audit rows, error envelopes, and structured logs all reference
the same id.

---

## Multi-tenancy in one paragraph

A tenant is a row in `public.tenants` with a dedicated PostgreSQL schema
named `tenant_<slug>`. Each tenant schema holds `tenant_roles`,
`tenant_role_permissions`, `tenant_membership_roles`. Incoming requests
carry the tenant slug via the `X-Tenant-Slug` header (default; configurable).
`TenantMiddleware` resolves the slug → `TenantContext`; the
`require_admin_context` dependency walks the four neutral providers
(auth → user → tenant → permissions), and the built-in
`PermissionProvider` issues `SET LOCAL search_path` on the
request-scoped session and loads the user's tenant-local roles +
permission keys. CRUD reads/writes happen on the same session, so tenant
data isolation comes from PostgreSQL, not from filters in Python.

PostgreSQL is required for real isolation. On SQLite (used for tests)
schema-per-tenant degrades to "single shared namespace" — fine for unit
tests, never for production.

---

## CLI

```bash
adminfoundry --help

# Database lifecycle
adminfoundry db upgrade-public
adminfoundry db upgrade-tenant <slug>
adminfoundry db upgrade-tenants

# Tenants
adminfoundry tenant create --name "Acme" --slug acme --owner-email owner@example.com
adminfoundry tenant list
adminfoundry tenant bootstrap <slug>

# Users
adminfoundry create-superadmin --email admin@example.com

# Permission catalog
adminfoundry permissions sync --app app:app
adminfoundry permissions list
adminfoundry permissions check admin.users.list

# Diagnostics
adminfoundry doctor
```

---

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — package layout + request lifecycle
- [`docs/security.md`](docs/security.md) — auth, permissions, secret handling, audit
- [`docs/tenancy.md`](docs/tenancy.md) — schema-per-tenant strategy + bootstrap
- [`docs/model-admin.md`](docs/model-admin.md) — `ModelAdmin` API reference
- [`docs/deployment.md`](docs/deployment.md) — Docker, env, health probes, observability
- [`docs/review-hardening-roadmap.md`](docs/review-hardening-roadmap.md) — consolidated roadmap: open pre-1.0 hardening, scope, phases, deliberate non-goals

---

## Development

```bash
git clone https://github.com/ArnWac/adminfoundry
cd adminfoundry
pip install -e ".[dev]"
docker-compose up -d db                       # optional, for postgres tests
export ADMINFOUNDRY_TEST_POSTGRES_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/adminfoundry
pytest -q
pytest -m postgres -q                         # only with the env var above
ruff check .
ruff format --check .
```

---

## License

MIT. See [`LICENSE`](LICENSE).
