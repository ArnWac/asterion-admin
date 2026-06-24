# Extensions

asterion ships an extension SPI so that optional behavior (CSV/XLSX
import-export, OAuth sign-in, custom auth backends, …) lives **outside** the
core package without forking it. The core never imports a concrete extension:
the dependency arrow is strictly **extension → core**, enforced by an AST-based
test in `tests/security/test_import_boundaries.py`.

This document covers the extension lifecycle, the registries an extension writes
into, how to ship ORM models, the `ExtensionContext`, ordering and error
semantics, the extensions that ship with the framework, and when *not* to write
one.

## Quick start

Subclass `AdminExtension`, set `name`, override the hooks you need, and pass an
instance to `create_admin(extensions=[…])`.

```python
from fastapi import FastAPI

from asterion import CoreAdminConfig, create_admin
from asterion.extensions import AdminExtension


class GreetingExtension(AdminExtension):
    name = "greeting"

    def register_permissions(self, registry):
        registry.register("greeting.say.hello")

    def register_routes(self, app: FastAPI, ctx):
        @app.get(f"{ctx.config.admin_api_prefix}/greeting/hello")
        async def hello() -> dict:
            return {"message": "Hello from an extension."}


app = create_admin(
    config=CoreAdminConfig.from_env(),
    extensions=[GreetingExtension()],
)
```

That is the whole contract.

## The lifecycle

`create_admin()` walks every extension through the hooks below, in order. Each
hook has a no-op default, so you override only what you need.

| # | Hook | Purpose | Frequency |
|---|---|---|---|
| 1 | `configure(config)` | Validate the framework config; raise to abort startup. | once per boot |
| 2 | `register_permissions(registry)` | Add namespaced permission keys (`"oauth.identities.list"`). | once |
| 3 | `register_protected_fields(registry)` | Add field names that must never serialize / log. | once |
| 4 | `register_contract_contributions(registry)` | Add a namespaced fragment to `GET /_contract`. | once |
| 5 | `register_admin_pages(registry)` | Register custom pages outside the CRUD schema. | once |
| 6 | `register_navigation(registry)` | Add permission-gated sidebar nav items. | once |
| 7 | `register_models()` | Return ORM classes whose tables this extension owns. | once |
| 8 | `register_routes(app, ctx)` | Mount routers on the FastAPI app. | once |
| 9 | `startup(app)` | Async resource setup (DB pools, JWKS clients, jobs). | once per process |
| 10 | `shutdown(app)` | Async teardown, in **reverse** order; failures logged, never raised. | once per process |

Between steps 6 and 7 the framework mirrors permission-bearing admin pages into
the navigation registry. After step 8 **all registries freeze** — any later
registration raises `RegistryFrozenError`. Only `register_routes` receives the
`app` directly; extension routes mount during the setup phase (before the
framework's CRUD routes), so an extension can add routes anywhere, including
under the admin prefix.

> **You may not need an extension just to mount a router.** CRUD routes are
> registered explicitly per resource, not as a greedy `/{resource}` catch-all,
> so an embedding app can mount its own routes under the admin prefix with a
> plain `app.include_router(...)` **after** `create_admin()`:
>
> ```python
> app = create_admin(config=..., register=...)
>
> from fastapi import APIRouter
> domain = APIRouter()
>
> @domain.get("/work-sessions/{session_id}")
> async def work_session(session_id: str): ...
>
> app.include_router(domain, prefix="/api/v1/admin")
> ```
>
> The only constraint: your path must not equal a **registered resource name**.
> Reach for `register_routes` when you need routes mounted during the setup
> phase (e.g. before other extensions, or gated on `ctx`); reach for
> `app.include_router` when you just want to add a router.

## The extension-side registries

Each registry is populated **only** during its corresponding hook, then frozen.
They live on the `AdminRuntime` (`request.app.state.asterion`) and are reachable
from routes and templates.

### PermissionRegistry

`runtime.permission_registry` — an in-memory bag of namespaced permission keys,
merged into the database `PermissionCatalog` by `asterion permissions sync`.

```python
def register_permissions(self, registry):
    registry.register("billing.invoices.list", "billing.invoices.refund")
```

#### Declaring permission keys without an extension

An app that only *embeds* asterion doesn't need an extension just to publish its
own permission keys. Pass them to `create_admin(permissions=...)` — either a
list of keys or a callback that receives the `PermissionRegistry`:

```python
app = create_admin(
    config=...,
    register=register_admins,
    permissions=[
        "timeclock.employee.read",
        "timeclock.employee.write",
        "timeclock.shift.close",
    ],
    # or a callback:
    # permissions=lambda reg: reg.register("timeclock.shift.close"),
)
```

These keys register into `runtime.permission_registry` **before** extensions'
`register_permissions` hooks run and are merged exactly like extension keys, so
`asterion permissions sync` writes them into the catalog and they become
assignable to tenant roles. Auto-derived `admin.<resource>.*` keys still come
from your `ModelAdmin`s automatically. Keys are validated
(`namespace.resource.action`) on registration; duplicates are idempotent.

### ProtectedFieldRegistry

`runtime.protected_fields` — a singleton seeded from `DEFAULT_PROTECTED_FIELDS`
(passwords, secrets, tokens). Extensions add more; every consumer (serializer,
contract router, audit sanitizer) sees the merged set.

```python
def register_protected_fields(self, registry):
    registry.register("client_secret", "refresh_token", "id_token")
```

### ContractContributionRegistry

`runtime.contract_contributions` — lets an extension add a namespaced top-level
key to `GET /_contract`. The UI uses these to render features the framework
itself doesn't know about (e.g. "show a Google login button").

```python
def register_contract_contributions(self, registry):
    registry.add("auth_oauth", {
        "providers": [
            {"id": "google", "label": "Google", "login_url": "/api/v1/oauth/google/login"},
        ],
    })
```

The shape of the fragment is entirely up to you — the framework only preserves
the namespacing.

### NavigationRegistry

`runtime.navigation` — permission-gated sidebar items. The UI fetches them via
`GET /api/v1/admin/_navigation`, server-side filtered to what the current
principal can use.

```python
def register_navigation(self, registry):
    registry.add_item(
        id="oauth.identities",                 # globally unique, dotted namespace
        label="External Identities",
        path="/admin/oauth/identities",
        permission="oauth.identities.list",    # principal needs this to see it
    )
```

Superadmins bypass the permission filter — see
[Auth architecture § Superadmin](auth-architecture.md#superadmin).

### AdminPageRegistry

`runtime.admin_pages` — custom pages outside the CRUD schema (a Reports view, a
status dashboard, a bulk-operation wizard). Each page declares a URL-safe `id`,
a sidebar `label`, and a `js_module` URL the built-in SPA dynamically imports
when the page is visited. Pages are served under the reserved
`{admin_ui_path}/_pages/{id}` prefix (mounted before the dynamic `/{resource}`
route, so a slug can never collide with a resource).

```python
from asterion.ui.admin_pages import AdminPage

def register_admin_pages(self, registry):
    registry.register(
        AdminPage(
            id="reports",                          # url-safe: [a-z][a-z0-9_-]*
            label="Reports",
            js_module="/admin/static/reports.js",  # SPA import()s this
            permission="reports.view",             # gates the nav item
            category="Tools",                      # optional grouping
        )
    )
```

A page that declares a `permission` is mirrored into the navigation registry
automatically, so it appears in the sidebar without a separate
`register_navigation` call. The `js_module` must export a `mount(root, ctx)`
function (or a default export of that shape); the SPA host imports it and hands
it the page's root element.

## Shipping ORM models

Extensions that ship database tables (the OAuth extension's `ExternalIdentity`,
for example) declare them via `register_models()`. The hook returns an iterable
of model classes; the framework records ownership on `runtime.extension_models`.

```python
from asterion.models.base import GlobalBase
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String


class MyExtensionThing(GlobalBase):
    __tablename__ = "my_extension_things"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))


class MyExtension(AdminExtension):
    name = "my_extension"

    def register_models(self):
        from my_extension import models   # import attaches the Table to GlobalBase.metadata
        return (models.MyExtensionThing,)
```

The model class **must** subclass `asterion.models.base.GlobalBase` (or
`TenantBase` for tenant-local data) so it lands on the shared metadata. Defining
it under your own `DeclarativeBase` would isolate it, and `create_all` /
autogenerate wouldn't see it.

### Migrations for extension tables

The framework ships **no** migrations for extension-owned tables — bundling them
would inflate every install with unused tables and couple framework releases to
extension schema changes. Each host opts in by importing:

1. Import the extension at the top of your shared Alembic `env.py` so
   `GlobalBase.metadata` sees the table. An embedding app keeps its own shared
   migrations env (it must not edit asterion's bundled, pip-installed one) that
   imports `asterion.models` plus the extension.
2. Run `alembic --autogenerate` against the env to create the revision.
3. Run `alembic upgrade head` in deployments.

asterion's own shared migrations (users, tenants, audit, tokens, 2FA,
password-reset) ship inside the wheel and are applied by `asterion db
upgrade-public`, so you don't manage those.

## ExtensionContext

The bundle handed to the `register_*` hooks. It carries the extension-side
registries plus the validated framework config.

```python
@dataclass
class ExtensionContext:
    config: CoreAdminConfig
    permissions: PermissionRegistry
    contract: ContractContributionRegistry
    navigation: NavigationRegistry
    protected_fields: ProtectedFieldRegistry
    admin_pages: AdminPageRegistry
    logger: logging.Logger
```

`register_permissions(registry)` receives `ctx.permissions` directly so the
common case stays a one-liner; `register_routes(app, ctx)` receives the whole
context.

## Ordering

Extensions are processed in **registration order** (the order they appear in
`extensions=[…]`). Each hook completes for every extension before the next hook
starts. Two consequences:

1. Permission keys, contract fragments, etc. from extension A are visible to
   extension B's `register_*` hooks if B comes after A — but not before. Don't
   rely on cross-extension state during `configure`.
2. `shutdown` runs in **reverse** order, so an extension that depends on
   another's resource releases first.

For cross-extension coordination (rare), put the dependency in `startup`, where
every registry is frozen and every extension's synchronous setup is complete.

## Errors

| Class | Raised when |
|---|---|
| `ExtensionError` | base class — for `isinstance` checks |
| `DuplicateExtensionError` | two extensions share the same `name` |
| `RegistryFrozenError` | a hook adds to a registry after freeze |
| `ExtensionDependencyError` | reserved for future cross-extension dependency declarations |

All live in `asterion.extensions.errors`.

## Bundled extensions

| Name | Module | What it does |
|---|---|---|
| `import_export` | `asterion.extensions.import_export` | CSV + XLSX import/export, per-admin |
| `auth_oauth` | `asterion.extensions.auth_oauth` | OIDC sign-in (Google ships; others via subclass) — see [OAuth / OIDC sign-in](auth-oauth.md) |
| `email` | `asterion.extensions.email` | SMTP / Resend / SES delivery + outbox — see [Email](email.md) |
| `rate_limit_redis` | `asterion.extensions.rate_limit_redis` | Distributed login rate limiter |

The first two are reference implementations of the "right way" to do
permissions, contract contributions, route mounting, and model registration —
read them if you're writing your own.

## When not to write an extension

Use an extension when a feature is **optional**, **third-party-ish**, or has
**external dependencies the framework should not impose**. Do *not* write one
for:

* Permission tweaks that belong on a `ModelAdmin` (`protected_fields`,
  `readonly_fields`, custom actions).
* Pure model registration — just call `registry.register(MyModelAdmin)` via the
  `register=` callable.
* Tenant-local business rules — that's `ModelAdmin` + tenant RBAC permissions.

If you find yourself reaching for an extension to override **core** behavior, the
right answer is usually a custom provider — see
[Auth architecture](auth-architecture.md).
