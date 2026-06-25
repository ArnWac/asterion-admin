# Multi-tenancy

asterion implements **schema-per-tenant** multi-tenancy on PostgreSQL: every
tenant gets its own database schema, and isolation is enforced by PostgreSQL's
`search_path` rather than by `WHERE tenant_id = …` filters in Python. This
document describes the data model, the request flow that scopes each query, how
to provision and bootstrap a tenant, and how members are managed.

> **PostgreSQL in production.** SQLite has no schemas, so it cannot enforce
> tenant isolation — it is supported for development and tests only. See
> [SQLite caveat](#sqlite-caveat).

## The data model

A **tenant** is a row in `public.tenants` (the global schema). Each tenant also
owns a PostgreSQL schema named `tenant_<slug>` that holds the tenant-local
tables.

| Schema | Tables |
|---|---|
| `public` | `users`, `tenants`, `tenant_memberships`, `permission_catalog`, `audit_logs`, `impersonation_logs` |
| `tenant_<slug>` | `tenant_roles`, `tenant_role_permissions`, `tenant_membership_roles` |

A `TenantMembership` row in the public schema links a user to a tenant. The
user's **roles inside that tenant** live in the tenant schema as
`TenantMembershipRole` rows pointing at `TenantRole`s. There is deliberately
**no `User.roles` relationship** — roles are tenant-local, not global, so the
same user can be an `owner` in one tenant and a `viewer` in another.

## How a request is scoped

Isolation rests on a single mechanism: the request-scoped session sets its
`search_path` to the resolved tenant's schema for the duration of the
transaction, so every unqualified (tenant-local) query resolves inside that
schema, while global tables carry an explicit `public.` qualifier.

```text
GET /api/v1/admin/posts
  Header: X-Tenant-Slug: acme
  Header: Authorization: Bearer <jwt>
        │
        ▼
TenantMiddleware
  └─ resolve_tenant("acme") → TenantContext(id, slug, schema_name="tenant_acme")
     stored on request.state.tenant
        │
        ▼
Depends(get_async_session)
  └─ opens an AsyncSession, BEGIN, and — on PostgreSQL with a resolved tenant —
     issues  SET LOCAL search_path TO "tenant_acme", public  for this txn.
        │
        ▼
Depends(require_admin_context)
  └─ AuthProvider.authenticate_request   → AuthIdentity
     UserProvider.get_by_id              → AdminPrincipal
     TenantProvider.resolve_tenant       → AdminTenant
     PermissionProvider.get_permissions  → frozenset[str]
        │
        ▼
Handler runs CRUD on the get_async_session session
  └─ tenant-local SELECT/INSERT (tenant_roles, …) resolve inside "tenant_acme"
     global SELECT (users, tenants) use the explicit "public." prefix
        │
        ▼
Response, COMMIT, session closed.
  SET LOCAL evaporates with the txn — the next request on the same pooled
  connection starts with a clean search_path.
```

### The three isolation invariants

The claim that *tenant isolation comes from PostgreSQL, not from Python filters*
rests on three invariants:

1. **`SET LOCAL search_path` is transaction-scoped** — it lives only for the
   current transaction and is gone on commit/rollback.
2. **The CRUD session sets the tenant `search_path` itself**, so every
   tenant-local read/write the handler runs resolves inside the tenant schema.
   Global tables carry an explicit `public.` qualifier (via `GlobalModel.metadata`)
   and are unaffected.
3. **Tenant-local tables carry no `tenant_id` column.** If they did, they could
   leak across schemas through a cross-schema join; without one, PostgreSQL's
   schema resolution is the only routing mechanism.

`tests/postgres/` proves all three against a real PostgreSQL instance (skipped
unless `ASTERION_TEST_POSTGRES_URL` is set): the `search_path` lifecycle and
schema-isolation primitives in `test_search_path_lifecycle.py` /
`test_tenant_isolation.py`, and the full request path — create under one tenant,
invisible to another — in `test_http_tenant_isolation.py`.

## Choosing a tenant resolver

The resolver extracts the tenant slug from each request. Configure it on
`CoreAdminConfig`:

```python
CoreAdminConfig(
    tenant_resolution="header",        # default
    tenant_header_name="X-Tenant-Slug",
)

CoreAdminConfig(
    tenant_resolution="subdomain",     # acme.example.com → "acme"
)
```

The resolver caches `slug → TenantContext` for 30 seconds in-process to avoid
hitting `public.tenants` on every request.

## Provisioning a tenant

```bash
# 1. Create the public Tenant row + (optionally) the owner membership.
asterion tenant create --name "Acme" --slug acme --owner-email owner@example.com

# 2. PostgreSQL only: provision the tenant schema + run tenant migrations.
asterion db upgrade-tenant acme

# 3. Seed default tenant-local roles. Done automatically by `tenant create`
#    on PostgreSQL; safe to re-run.
asterion tenant bootstrap acme
```

### What bootstrap does

`bootstrap_tenant()` performs the following, all idempotent:

1. Sync `PermissionCatalog` from your registry (only when a registry is passed
   programmatically — the CLI form does not auto-discover; run
   `asterion permissions sync --app app:app` first for useful defaults).
2. `CREATE SCHEMA IF NOT EXISTS "tenant_acme"`.
3. Run the tenant migrations against the schema: asterion's bundled **framework
   tenant base** first (RBAC + `tenant_audit_logs`, tracked in its own
   `alembic_version_asterion_tenant`), **then** your app's `alembic_tenant.ini`
   tree (your domain tables, tracked in `alembic_version`). The framework base
   is always applied, so a tenant schema can never be missing a framework table
   because the app's tree forgot to import a model. See
   [Deployment](deployment.md).
4. Create three default roles inside `tenant_acme`:
   * `owner` — always granted `admin.*` (plus everything in the catalog).
   * `admin` — all catalog keys **except** `admin.audit_logs.delete` and
     `admin.users.delete`.
   * `viewer` — catalog keys ending in `.list`.
5. Assign the `owner` role to the optional owner membership.
6. Write an audit row.

Steps 4–6 are idempotent; steps 2–3 use `IF NOT EXISTS` / Alembic versioning, so
re-running `bootstrap` is always safe.

## Member management

Once a tenant exists, its own operators onboard further admin users — no
superadmin or CLI round-trip per member. The endpoints live under the admin API
prefix and are strictly scoped to the caller's tenant.

| Method | Path | Permission key |
|---|---|---|
| `GET` | `/api/v1/admin/_members` | `admin.tenant_members.list` |
| `POST` | `/api/v1/admin/_members` | `admin.tenant_members.create` |
| `PATCH` | `/api/v1/admin/_members/{membership_id}` | `admin.tenant_members.update` |
| `DELETE` | `/api/v1/admin/_members/{membership_id}` | `admin.tenant_members.delete` |

These keys are built-in (always in the catalog). Bootstrap seeds them onto
`owner` (via `admin.*`) and `admin`; `viewer` gets only `.list`.

### Inviting a member

`POST` takes `{email, full_name?, role_ids?}` and branches on whether the email
already maps to a global user:

* **Existing global user** → the membership is created or reactivated
  (idempotent) and roles are assigned. Response: `{"invited": false, …}`.
* **Unknown email** → an **inactive, passwordless** global `User` is created,
  the membership is added, and a single-use **invite token** is issued. The raw
  token is handed to the configured [`InviteNotifier`](#invite-delivery).
  Response: `{"invited": true, …}`.

The invitee completes onboarding at the existing
`POST /api/v1/auth/password-reset/confirm` with their token plus a new password,
which both sets the password **and** activates the account.

### Isolation guarantees

A `membership_id` from another tenant resolves to **`404`** (never `403`), so
the endpoint never confirms that an out-of-tenant row exists. `DELETE` removes
the membership and its role links but leaves the global `User` intact — that
user may belong to other tenants.

### Invite delivery

The framework owns the invite-token lifecycle but not delivery (email/SMS is
app-specific), so it routes through an `InviteNotifier` you supply:

```python
from asterion import create_admin

class EmailInviteNotifier:
    async def send_invite(self, *, email, token, tenant_slug=None, request=None):
        link = f"https://app.example.com/accept-invite?token={token}"
        await send_email(to=email, subject="You're invited", body=link)

app = create_admin(invite_notifier=EmailInviteNotifier())
```

The default `LoggingInviteNotifier` logs the token at `WARNING` for local
development — **unsafe for production** (tokens in logs are a credential leak).
Invite tokens last `ASTERION_INVITE_TOKEN_EXPIRE_MINUTES` (default 7 days).

For real delivery, the bundled email extension (`asterion-admin[email]`) ships
an SMTP notifier that satisfies **both** the invite and password-reset
Protocols — wire one instance into both keywords:

```python
from asterion import create_admin
from asterion.extensions.email import SmtpEmailNotifier

mailer = SmtpEmailNotifier.from_env()   # ASTERION_SMTP_HOST / _FROM / …
app = create_admin(
    config=...,
    password_reset_notifier=mailer,
    invite_notifier=mailer,
)
```

Set `ASTERION_INVITE_URL` / `ASTERION_RESET_URL` (link templates with a
`{token}` placeholder) so the emails carry a real link. See
[Email](email.md) for SMTP / Resend / SES transports, overridable templates,
custom events, and the transactional outbox.

## SQLite caveat

SQLite has no schemas. On SQLite:

* `bootstrap_tenant()` is a **no-op** beyond returning early;
* `SET LOCAL search_path` is never issued;
* all "tenant-local" tables share one namespace.

That is fine for unit tests of seeding logic, CRUD payload validation, and route
shape, but it does **not** prove tenant isolation — the `tests/postgres/` suite
is what proves that. Do not run a production multi-tenant deployment on SQLite.

## See also

* [Auth architecture](auth-architecture.md) — the four providers that resolve
  identity, tenant, and permissions per request.
* [Security model](security.md) — permission keys, field protection, audit.
* [Deployment](deployment.md) — migrations, `db upgrade-tenant(s)`, backups.
