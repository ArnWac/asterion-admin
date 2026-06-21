# Extensions

`asterion` ships an extension SPI so optional behaviour (CSV
import/export, OAuth, custom auth backends, …) lives **outside** the
core package without having to fork it.

The core never imports a concrete extension. The dependency arrow is
strictly **extension → core**, enforced by an AST-based test in
`tests/security/test_import_boundaries.py`.

---

## Quick start

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

That's the whole contract. Subclass `AdminExtension`, set `name`, override
the hooks you need, pass an instance to `create_admin(extensions=[…])`.

---

## The lifecycle

`create_admin()` walks every extension through the hooks below, in
order. Each hook has a no-op default — override only what you need.

| # | Hook | Purpose | Frequency |
|---|---|---|---|
| 1 | `configure(config)` | Validate the framework config; raise to abort startup | once per app boot |
| 2 | `register_permissions(registry)` | Add namespaced permission keys (`"oauth.identities.list"`) | once |
| 3 | `register_protected_fields(registry)` | Add field names that must never serialize / log (`"hashed_password"`) | once |
| 4 | `register_contract_contributions(registry)` | Add a namespaced fragment to `GET /_contract` for the UI to consume | once |
| 5 | `register_admin_pages(registry)` | Register custom pages outside the CRUD schema (Roadmap 5.6) | once |
| 6 | `register_navigation(registry)` | Add permission-gated sidebar nav items | once |
| — | _Framework step:_ permission-bearing admin pages are mirrored into the navigation registry | | |
| 7 | `register_models()` | Return ORM classes whose tables this extension owns | once |
| 8 | `register_routes(app, ctx)` | Mount routers on the FastAPI app | once |
| — | **All registries freeze here.** Any later attempt to register raises `RegistryFrozenError`. | | |
| 9 | `startup(app)` | Async resource setup (DB pools, JWKS clients, background jobs) | once per process |
| 10 | _(requests served)_ | | |
| 11 | `shutdown(app)` | Async resource teardown — called in **reverse** registration order; failures logged but never raised | once per process |

Only `register_routes` receives the `app` directly. Extension routes are
mounted during the setup phase (before the framework's CRUD routes), so an
extension can add routes anywhere — including under the admin prefix.

> **You may not need an extension just to mount a router.** As of 0.1.6 the CRUD
> routes are registered explicitly per registered resource, not as a greedy
> `/{resource}` catch-all. So an embedding app can mount its own routes under
> the admin prefix with a plain `app.include_router(...)` **after**
> `create_admin()`:
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
> The only constraint: your path must not equal a **registered resource name**
> (that's a real, explicit conflict — rename the resource or the route). Reach
> for `register_routes` when you need routes mounted during the setup phase
> (e.g. before other extensions, or gated on `ctx`); reach for
> `app.include_router` when you just want to add a router.

---

## The extension-side registries

Each is populated **only** during the corresponding hook, then
frozen. They live on the `AdminRuntime` (`request.app.state.asterion`)
and are reachable from routes / templates.

### `PermissionRegistry` — `runtime.permission_registry`

In-memory bag of namespaced permission keys. Used by the CLI's
`permissions sync` command to merge extension-owned keys into the
database `PermissionCatalog`.

```python
def register_permissions(self, registry):
    registry.register("billing.invoices.list", "billing.invoices.refund")
```

#### Declaring permission keys WITHOUT an extension

An app that only *embeds* asterion doesn't need to write an extension just to
publish its own permission keys. Pass them to `create_admin(permissions=...)` —
either a list of keys or a callback that receives the `PermissionRegistry`:

```python
from asterion import create_admin

app = create_admin(
    config=...,
    register=register_admins,
    # custom keys in your own namespace (auto-derived admin.<resource>.* keys
    # still come from your ModelAdmins automatically):
    permissions=[
        "timeclock.employee.read",
        "timeclock.employee.write",
        "timeclock.shift.close",
    ],
    # or, equivalently, a callback:
    # permissions=lambda reg: reg.register("timeclock.shift.close"),
)
```

These keys are registered into `runtime.permission_registry` **before**
extensions' `register_permissions` hooks run, and are merged by
`generate_permission_keys(...)` exactly like extension keys — so
`asterion permissions sync` writes them into the `PermissionCatalog` and they
become assignable to tenant roles. Keys are validated (`namespace.resource.action`
shape) on registration, and duplicates — including a key also declared by an
extension — are idempotent (the registry is a set).

### `ProtectedFieldRegistry` — `runtime.protected_fields`

Singleton seeded from `DEFAULT_PROTECTED_FIELDS` (passwords, secrets,
tokens). Extensions can add more — every consumer of the registry
(serializer, contract router, audit sanitizer) sees the merged set.

```python
def register_protected_fields(self, registry):
    registry.register("client_secret", "refresh_token", "id_token")
```

### `ContractContributionRegistry` — `runtime.contract_contributions`

Lets an extension add a namespaced top-level key to `GET /_contract`.
The UI uses these to render features the framework itself doesn't know
about (e.g. "show a Google login button").

```python
def register_contract_contributions(self, registry):
    registry.add("auth_oauth", {
        "providers": [
            {"id": "google", "label": "Google", "login_url": "/api/v1/oauth/google/login"},
        ],
    })
```

The shape of the fragment is entirely up to you — the framework only
preserves the namespacing.

### `NavigationRegistry` — `runtime.navigation`

Permission-gated sidebar items. The UI fetches them via
`GET /api/v1/admin/_navigation`, server-side filtered to what the
current principal can actually use.

```python
def register_navigation(self, registry):
    registry.add_item(
        id="oauth.identities",            # globally unique, dotted namespace required
        label="External Identities",
        path="/admin/oauth/identities",
        permission="oauth.identities.list",   # principal needs this perm to see the item
    )
```

Superadmins bypass the permission filter — see [auth-architecture.md](auth-architecture.md).

### `AdminPageRegistry` — `runtime.admin_pages`

Custom pages outside the CRUD schema (Roadmap 5.6) — a Reports view, a
status dashboard, a bulk-operation wizard. Each page declares a URL-safe
`id`, a sidebar `label`, and a `js_module` URL the built-in SPA
dynamically imports when the page is visited. Pages are served under the
reserved `{admin_ui_path}/_pages/{id}` prefix (mounted before the dynamic
`/{resource}` route, so a slug can never collide with a resource).

```python
from asterion.ui.admin_pages import AdminPage

def register_admin_pages(self, registry):
    registry.register(
        AdminPage(
            id="reports",                       # url-safe: [a-z][a-z0-9_-]*
            label="Reports",
            js_module="/admin/static/reports.js",  # SPA import()s this
            permission="reports.view",          # gates the nav item
            category="Tools",                   # optional grouping
        )
    )
```

A page that declares a `permission` is mirrored into the navigation
registry automatically, so it shows up in the sidebar without a separate
`register_navigation` call. The `js_module` must export a
`mount(root, ctx)` function (or a default export of that shape); the SPA
host (`views/page.js`) imports it and hands it the page's root element.

---

## ORM models — `register_models()`

Extensions that ship database tables (the OAuth extension's
`ExternalIdentity`, for example) declare them via `register_models()`.
The hook returns an iterable of model classes; the framework stashes
them on `runtime.extension_models` so tooling can answer "which
extension owns table X".

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
        # Importing the model module attaches the Table to
        # GlobalBase.metadata at class-definition time. Returning the
        # class records ownership on runtime.extension_models.
        from my_extension import models
        return (models.MyExtensionThing,)
```

The model class **must** subclass `asterion.models.base.GlobalBase`
(or `TenantBase` for tenant-local data) so it lands on the shared
metadata. Defining it under your own `DeclarativeBase` would put it in
its own private namespace and `create_all` / autogenerate wouldn't see it.

### Migration story

The framework ships **no** migrations for extension-owned tables.
Apps that wire an extension are responsible for generating their own
revisions:

1. Import the extension at the top of your Alembic shared `env.py` so
   `GlobalBase.metadata` sees the table. In the asterion repo that env is
   `asterion/_migrations/shared/env.py`; an **embedding app** keeps its own
   shared migrations env (it must not edit asterion's bundled, pip-installed
   one) that imports `asterion.models` plus the extension.
2. Run `alembic --autogenerate` against the env. The new revision
   creates the extension's tables.
3. Run `alembic upgrade head` in deployments.

(asterion's *own* shared migrations — users, tenants, audit, tokens, 2FA,
password_reset — ship inside the wheel and are applied package-relatively by
`asterion db upgrade-public`, so you don't manage those.)

This is intentional: bundling migrations for extension tables would
inflate every asterion installation with tables the host might
never use, and would couple framework releases to extension schema
changes. Each host opts in by importing.

---

## `ExtensionContext`

The bundle handed to `register_*` hooks. Carries the extension-side
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

`register_permissions(registry)` receives `ctx.permissions` directly so
the common case stays a one-liner; `register_routes(app, ctx)` receives
the whole context.

---

## Ordering

Extensions are processed in **registration order** — i.e. the order they
appear in the `extensions=[…]` list passed to `create_admin()`. Each
hook completes for every extension before the next hook starts. Two
consequences:

1. Permission keys, contract fragments, etc. from extension A are
   visible to extension B's `register_*` hooks if B comes after A — but
   not before. Don't rely on cross-extension state during `configure`.
2. `shutdown` runs in **reverse** registration order, so an extension
   that depends on another's resource will release first.

If you need cross-extension coordination (rare), put the dependency in
`startup` where every registry is frozen and every extension's
synchronous setup is already complete.

---

## Errors

| Class | Raised when |
|---|---|
| `ExtensionError` | base class — for `isinstance` checks |
| `DuplicateExtensionError` | two extensions share the same `name` |
| `RegistryFrozenError` | a hook tries to add to a registry after freeze |
| `ExtensionDependencyError` | reserved for future cross-extension dependency declarations |

All live in `asterion.extensions.errors`.

---

## Ships-with extensions

| Name | Module | What it does |
|---|---|---|
| `import_export` | `asterion.extensions.import_export` | CSV + XLSX import/export, per-admin |
| `auth_oauth` | `asterion.extensions.auth_oauth` | OIDC sign-in (Google ships, GitHub/Microsoft/etc. via subclass) — see [auth-oauth.md](auth-oauth.md) |

Both are reference implementations of "the right way" to do
permissions / contract contributions / route mounting / model
registration. Read them if you're writing your own.

---

## When NOT to write an extension

Use an extension when the feature is **optional**, **third-party-ish**,
or has **external dependencies the framework should not impose**.

Don't write an extension for:

- Permission tweaks that belong on a `ModelAdmin` (use `protected_fields`,
  `readonly_fields`, custom actions).
- Pure model registration — just call `registry.register(MyModelAdmin)`
  via the `register=` callable.
- Tenant-local business rules — that's `ModelAdmin` + tenant RBAC
  permissions.

If you find yourself reaching for an extension to override **core**
behaviour, the right answer is usually a custom provider — see
[auth-architecture.md](auth-architecture.md).
