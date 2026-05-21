# multi_tenant — schema-per-tenant issue tracker

A two-tenant SaaS demo showing schema-per-tenant isolation, owner roles,
and tenant-scoped models in `~200` lines of app code.

## What's inside

- **Two tenants**, `acme` and `globex`, each in its own PostgreSQL schema
  (`tenant_acme`, `tenant_globex`) provisioned at startup by `bootstrap_tenant`.
- **Tenant-scoped models**: `Project` and `Ticket` inherit from `TenantModel`,
  so their tables live inside each tenant schema. There is no `tenant_id`
  column — isolation is enforced by `SET LOCAL search_path` applied per
  request by `TenantMiddleware`.
- **One owner user per tenant**, assigned to the seeded `owner` role
  (which carries the `admin.*` wildcard permission).
- **One global superadmin** that bypasses tenant scoping and can inspect
  every schema from the admin UI.
- **A custom `CloseTicketsAction`** that closes the selected tickets in
  bulk — demonstrates how to override `AdminAction.execute`.
- **Global-schema admins** for `User`, `Tenant`, `TenantMembership`,
  `AuditLog`, `ImpersonationLog` — registered from `global_admins.py`.
  The audit/impersonation admins are read-only in the UI (every field
  listed in `readonly_fields`). The framework doesn't ship these by
  default; the file is structured so it can be lifted into
  `adminfoundry/builtins/admin.py` later.

## Requirements

PostgreSQL 14+. The schema-per-tenant strategy needs `SET LOCAL
search_path` and `CREATE SCHEMA`, which SQLite cannot express; the seed
script refuses to run without a PostgreSQL `DATABASE_URL`.

Spin up a throwaway database:

```bash
docker run --rm -d \
    --name adminfoundry-pg \
    -e POSTGRES_PASSWORD=postgres \
    -e POSTGRES_DB=adminfoundry \
    -p 5432:5432 \
    postgres:16
```

## Run

```bash
export DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/adminfoundry
export SECRET_KEY=$(openssl rand -hex 32)

uvicorn examples.multi_tenant.app:app --reload
```

Open <http://127.0.0.1:8000/admin> and sign in with the credentials
printed to the console. The first boot seeds two tenants + sample
projects + tickets; subsequent boots are no-ops.

## Trying it from the API

Every admin/auth endpoint can be scoped to a tenant via the
`X-Tenant-Slug` header. Without the header you act in the public schema —
useful for superadmins managing tenants and users.

```bash
# 1. log in as a tenant owner
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/v1/auth/login \
    -H 'Content-Type: application/json' \
    -d '{"email":"owner@acme.test","password":"owner123"}' | jq -r .access_token)

# 2. list projects in the acme schema
curl -H "Authorization: Bearer $TOKEN" \
     -H "X-Tenant-Slug: acme" \
     http://127.0.0.1:8000/api/v1/admin/projects

# 3. same call without the header → public schema; owner has no
#    permissions there and gets a 403.
curl -H "Authorization: Bearer $TOKEN" \
     http://127.0.0.1:8000/api/v1/admin/projects
```

## Seeding without booting the server

```bash
python -m examples.multi_tenant.seed
```

The script is idempotent — re-running on an existing database adds only
missing rows.

## How tenant bootstrap works here

The seed walks the framework's bootstrap APIs in order:

1. `create_tenant_record(public_db, name, slug)` — writes the public
   `tenants` row.
2. Owner user is created if missing.
3. `assign_owner_membership(public_db, tenant, user)` — writes the public
   `tenant_memberships` row.
4. `bootstrap_tenant(slug, public_db, owner_membership_id, database_url, registry)`:
   - Syncs `PermissionCatalog` from the passed registry (so the seeded
     `admin` and `viewer` roles end up with per-resource keys).
   - `CREATE SCHEMA IF NOT EXISTS tenant_<slug>`.
   - Runs the framework's tenant Alembic migrations against the schema.
   - Seeds the three default tenant roles + role permissions.
   - Assigns the owner membership to the `owner` role.
5. The demo additionally `CREATE TABLE`s its own `projects` and `tickets`
   inside the new schema and inserts sample rows — production apps would
   express this in tenant-side Alembic migrations instead.
