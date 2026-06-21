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
  └─► opens an AsyncSession, BEGIN, and — on PostgreSQL with a resolved
      tenant — issues SET LOCAL search_path TO "tenant_acme", public for
      this transaction. This is the session the handler runs CRUD on.
            │
            ▼
Depends(require_admin_context)
  └─► AuthProvider.authenticate_request(request)            → AuthIdentity
  └─► UserProvider.get_by_id(identity.user_id)              → AdminPrincipal
  └─► TenantProvider.resolve_tenant(request)                → AdminTenant
  └─► PermissionProvider.get_permissions(principal, tenant) → frozenset[str]
        (BuiltinPermissionProvider loads TenantRole +
         TenantRolePermission for the membership; its own short-lived
         session sets the same search_path for that lookup.)
  └─► returns AdminContext(principal, tenant, permissions, …)
            │
            ▼
Handler reads/writes via the get_async_session session
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
2. The request-scoped CRUD session (`get_async_session`) sets the tenant
   `search_path` itself, so every tenant-local SELECT/INSERT the handler
   runs resolves inside the tenant schema. Global tables carry an explicit
   `public.` qualifier (via `GlobalModel.metadata`) and are unaffected.
3. Tenant-local tables don't carry a `tenant_id` column. If they did,
   they could leak across schemas via cross-schema joins; without it,
   PostgreSQL's schema resolution is the only routing mechanism.

`tests/postgres/` proves all three against a real PG instance (skipped
without `ASTERION_TEST_POSTGRES_URL`): the `search_path` lifecycle and
schema isolation primitives in `test_search_path_lifecycle.py` /
`test_tenant_isolation.py`, and the full request path — create under one
tenant, invisible to another — in `test_http_tenant_isolation.py`.

## Provisioning a tenant

```bash
# 1. Create the public Tenant row + (optionally) the owner membership.
asterion tenant create \
    --name "Acme" --slug acme \
    --owner-email owner@example.com

# 2. PostgreSQL only: provision the tenant schema + run tenant migrations.
asterion db upgrade-tenant acme

# 3. Seed default tenant-local roles. Done automatically by `tenant create`
#    on PostgreSQL; safe to re-run.
asterion tenant bootstrap acme
```

The bootstrap step:

1. Sync `PermissionCatalog` from your registry (only if a registry is
   passed programmatically — the CLI form does not auto-discover, run
   `asterion permissions sync --app app:app` first for useful
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

## Member management (tenant-scoped)

Once a tenant exists, its operators onboard further admin users themselves —
no superadmin or CLI round-trip per member. The endpoints live under the admin
API prefix and are strictly scoped to the caller's tenant:

| Method | Path | Permission key |
|---|---|---|
| `GET` | `/api/v1/admin/_members` | `admin.tenant_members.list` |
| `POST` | `/api/v1/admin/_members` | `admin.tenant_members.create` |
| `PATCH` | `/api/v1/admin/_members/{membership_id}` | `admin.tenant_members.update` |
| `DELETE` | `/api/v1/admin/_members/{membership_id}` | `admin.tenant_members.delete` |

These keys are built-in (always in the catalog) and bootstrap seeds them onto
`owner` (via `admin.*`) and `admin`; `viewer` gets only `.list`.

`POST` takes `{email, full_name?, role_ids?}`:

- **Existing global user** → the membership is created/reactivated
  (idempotent), roles assigned. Response `{"invited": false, ...}`.
- **Unknown email** → an **inactive, passwordless** global `User` is created,
  the membership is added, and a single-use **invite token** is issued. The
  raw token is handed to the configured `InviteNotifier` (see below). Response
  `{"invited": true, ...}`.

The invitee completes onboarding at the existing
`POST /api/v1/auth/password-reset/confirm` with their token + a new password —
which sets the password **and activates** the account.

Cross-tenant isolation: a `membership_id` from another tenant resolves to
`404` (never `403`), so the endpoint never confirms out-of-tenant rows exist.
`DELETE` removes the membership and its role links but leaves the global
`User` intact — it may belong to other tenants.

### Invite delivery

The framework owns the invite token lifecycle but not delivery (email/SMS is
app-specific), so it goes through an `InviteNotifier` you supply:

```python
from asterion import create_admin

class EmailInviteNotifier:
    async def send_invite(self, *, email, token, tenant_slug=None, request=None):
        link = f"https://app.example.com/accept-invite?token={token}"
        await send_email(to=email, subject="You're invited", body=link)

app = create_admin(invite_notifier=EmailInviteNotifier())
```

The default `LoggingInviteNotifier` logs the token at WARNING for local
development — **unsafe for production** (tokens in logs are a credential leak).
Invite tokens last `ASTERION_INVITE_TOKEN_EXPIRE_MINUTES` (default 7 days).

For real delivery the framework bundles an optional SMTP notifier
(`asterion-admin[email]`) that satisfies **both** the invite and
password-reset Protocols — wire one instance into both keywords:

```python
from asterion import create_admin
from asterion.extensions.email import SmtpEmailNotifier

mailer = SmtpEmailNotifier.from_env()  # ASTERION_SMTP_HOST / _FROM / ...
app = create_admin(
    config=...,
    password_reset_notifier=mailer,
    invite_notifier=mailer,
)
```

Set `ASTERION_INVITE_URL` / `ASTERION_RESET_URL` (link templates with a
`{token}` placeholder) so the emails carry a real link. To brand the emails,
subclass `SmtpEmailNotifier` and override `render_invite` / `render_reset`
(they return an `EmailContent` with subject + plaintext + optional HTML).
Apps with a transactional-email provider (SES/Postmark/SendGrid) instead pass
their own notifier — or a `transport=` callable to route the built message
through their own pipeline.

#### Custom email events

Beyond reset + invite, the app can send its own emails (welcome, receipt,
"export ready", …) through the same SMTP transport — register a renderer per
event and call `send`:

```python
from asterion.extensions.email import EmailContent

mailer.register_template(
    "welcome",
    lambda to, ctx: EmailContent(
        subject="Welcome!",
        text=f"Hi {ctx.get('name', to)}, glad you're here.",
    ),
)

# anywhere in your app (e.g. after creating an account):
await mailer.send("welcome", "newuser@example.com", context={"name": "Sam"})
```

`send(event, to, context=...)` renders the registered template and delivers it
the same way as reset/invite. Renderers can be passed up front via
`SmtpEmailNotifier(templates={...})`, and a subclass can override
`render_event(event, to, context)` to dispatch through a template engine.
Reset and invite keep their dedicated `send_reset` / `send_invite` (they're
framework SPI methods) but share the identical build + transport path.

See [`docs/email.md`](email.md) for the full picture: SMTP / Resend / SES
transports, overridable Jinja templates, custom events, and the transactional
**outbox** (`OutboxEmailNotifier` + `process_outbox`) for robust, retried
delivery.

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
