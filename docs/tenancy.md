# Multi-tenancy (schema-per-tenant)

## Model

A **tenant** is a row in `public.tenants` (the "global" schema). Each
tenant gets its own PostgreSQL schema named `tenant_<slug>` holding the
**tenant-local** tables:

| Schema | Table |
|---|---|
| public | users, tenants, tenant_memberships, permission_catalog, audit_logs, impersonation_logs |
| tenant_<slug> | tenant_roles, tenant_role_permissions, tenant_membership_roles |

A `TenantMembership` row in the public schema links a user to a tenant.
The user's roles inside that tenant live in the tenant schema as
`TenantMembershipRole` rows pointing at `TenantRole`s. **No `User.roles`
relationship** — roles are tenant-local, not global.

## Request flow

```text
GET /api/v1/admin/posts
  Header: X-Tenant-Slug: acme
  Header: Authorization: Bearer <jwt>
            │
            ▼
TenantMiddleware
  └─► resolver.resolve_tenant("acme")  → TenantContext(id, slug, schema_name="tenant_acme", …)
      stored on request.state.tenant
            │
            ▼
Depends(get_async_session)
  └─► opens an AsyncSession, BEGIN
            │
            ▼
Depends(require_admin_context)
  └─► AuthProvider.authenticate_request(request)            → AuthIdentity
  └─► UserProvider.get_by_id(identity.user_id)              → AdminPrincipal
  └─► TenantProvider.resolve_tenant(request)                → AdminTenant
  └─► PermissionProvider.get_permissions(principal, tenant) → frozenset[str]
        (BuiltinPermissionProvider issues SET LOCAL search_path
         TO "tenant_acme", public — scoped to the txn — and loads
         TenantRole + TenantRolePermission for the membership.)
  └─► returns AdminContext(principal, tenant, permissions, …)
            │
            ▼
Handler reads/writes via the SAME session
  └─► SELECTs/INSERTs against tenant_roles, etc. naturally hit "tenant_acme"
  └─► SELECTs against users/tenants explicitly use "public" prefix via GlobalModel.metadata
            │
            ▼
Response, COMMIT, session closed.
SET LOCAL evaporates with the txn — next request on the same pooled
connection starts with a fresh search_path.
```

The plan's central architectural claim — *tenant isolation comes from
PostgreSQL, not from filters in Python* — rests on three invariants:

1. `SET LOCAL search_path` lives **only** for the current transaction.
2. The CRUD session and the `BuiltinPermissionProvider` session are the
   **same object** (so the search_path it sets applies to subsequent
   CRUD queries).
3. Tenant-local tables don't carry a `tenant_id` column. If they did,
   they could leak across schemas via cross-schema joins; without it,
   PostgreSQL's schema resolution is the only routing mechanism.

`tests/postgres/` proves all three against a real PG instance (skipped
without `ADMINFOUNDRY_TEST_POSTGRES_URL`).

## Provisioning a tenant

```bash
# 1. Create the public Tenant row + (optionally) the owner membership.
adminfoundry tenant create \
    --name "Acme" --slug acme \
    --owner-email owner@example.com

# 2. PostgreSQL only: provision the tenant schema + run tenant migrations.
adminfoundry db upgrade-tenant acme

# 3. Seed default tenant-local roles. Done automatically by `tenant create`
#    on PostgreSQL; safe to re-run.
adminfoundry tenant bootstrap acme
```

The bootstrap step:

1. Sync `PermissionCatalog` from your registry (only if a registry is
   passed programmatically — the CLI form does not auto-discover, run
   `adminfoundry permissions sync --app app:app` first for useful
   defaults).
2. `CREATE SCHEMA IF NOT EXISTS "tenant_acme"`.
3. Run `alembic_tenant.ini` against the schema.
4. Inside `tenant_acme`, create three default roles:
   - `owner`  → always granted `admin.*` (plus everything in the catalog).
   - `admin`  → all catalog keys minus `admin.audit_logs.delete` and `admin.users.delete`.
   - `viewer` → catalog keys ending in `.list`.
5. Assign the owner role to the optional owner membership.
6. Write an audit row.

Steps 4–6 are idempotent; step 2–3 use `IF NOT EXISTS` / Alembic
versioning so re-running is safe.

## SQLite caveat

SQLite has no schemas. On SQLite:

- `bootstrap_tenant()` is a **no-op** beyond returning early.
- `SET LOCAL search_path` is never issued.
- All "tenant-local" tables share one namespace.

That is fine for unit tests of seeding logic / CRUD payload validation /
route shape, but it does NOT prove tenant isolation. The integration
suite in `tests/postgres/` is what proves it. Don't run a production
multi-tenant deployment on SQLite.

## Choosing a tenant resolver

```python
CoreAdminConfig(
    tenant_resolution="header",     # default
    tenant_header_name="X-Tenant-Slug",
)
# or
CoreAdminConfig(
    tenant_resolution="subdomain",  # acme.example.com → "acme"
)
```

The resolver caches `(slug → TenantContext)` for 30 seconds in-process
to avoid hitting `public.tenants` on every request.
