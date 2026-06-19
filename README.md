# asterion

[![CI](https://github.com/ArnWac/asterion-admin/actions/workflows/ci.yml/badge.svg)](https://github.com/ArnWac/asterion-admin/actions/workflows/ci.yml)

A contract-driven FastAPI admin framework for SQLAlchemy applications.

Register your models declaratively and get a generated admin: CRUD routes,
permission-key authorization, multi-tenant scoping (PostgreSQL schema-per-tenant),
audit log, impersonation, a small built-in UI shell, and a JSON contract API
that any frontend can consume.

---

## Install

```bash
# PostgreSQL deployment
pip install asterion-admin[postgres]

# SQLite (development / testing)
pip install asterion-admin[sqlite]
```

Requires Python 3.11+.

---

## Quickstart

```python
# app.py
from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from asterion import CoreAdminConfig, ModelAdmin, create_admin
from asterion.actions import BulkDeleteAction
from asterion.models.base import GlobalModel


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
export ASTERION_DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/myapp"
export ASTERION_SECRET_KEY="$(openssl rand -hex 32)"

asterion db upgrade-public
asterion permissions sync --app app:app
asterion create-superadmin --email admin@example.com

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
asterion --help

# Database lifecycle
asterion db upgrade-public
asterion db upgrade-tenant <slug>
asterion db upgrade-tenants

# Tenants
asterion tenant create --name "Acme" --slug acme --owner-email owner@example.com
asterion tenant list
asterion tenant bootstrap <slug>

# Users
asterion create-superadmin --email admin@example.com

# Permission catalog
asterion permissions sync --app app:app
asterion permissions list
asterion permissions check admin.users.list

# Diagnostics
asterion doctor
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
git clone https://github.com/ArnWac/asterion-admin
cd asterion
pip install -e ".[dev]"
docker-compose up -d db                       # optional, for postgres tests
export ASTERION_TEST_POSTGRES_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/asterion
pytest -q
pytest -m postgres -q                         # only with the env var above
ruff check .
ruff format --check .
```

---

## License

MIT. See [`LICENSE`](LICENSE).
