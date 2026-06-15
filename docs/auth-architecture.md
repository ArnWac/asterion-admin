# Auth architecture

`adminfoundry` separates **identity** (who is calling) from **the
framework's view of that identity** (what it can see) so apps with their
own user/auth systems can integrate without forking the package.

The whole story:

```text
Request → AuthProvider.authenticate_request   → AuthIdentity (user_id + claims)
        → UserProvider.get_by_id              → AdminPrincipal (neutral DTO)
        → TenantProvider.resolve_tenant       → AdminTenant | None
        → PermissionProvider.get_permissions  → frozenset[str]
        ────────────────────────────────────────────────────────────────
                                  ▼
                         AdminContext(principal, tenant, permissions, …)
                                  ▼
                       Every route reads exactly this.
```

The four providers are the **only** seams between `adminfoundry` and the
host application's identity layer. Routes never look at JWT internals,
SQLAlchemy User rows, tenant memberships, or role-permission joins.

---

## The neutral DTOs

```python
@dataclass(frozen=True, slots=True)
class AuthIdentity:
    user_id: str
    claims: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True, slots=True)
class AdminPrincipal:
    id: str
    email: str | None = None
    display_name: str | None = None
    is_active: bool = True
    is_superadmin: bool = False

@dataclass(frozen=True, slots=True)
class AdminTenant:
    id: str
    slug: str
    name: str | None = None
```

These live in [`adminfoundry/providers/base.py`](../adminfoundry/providers/base.py).
The framework only ever sees these — never a concrete `User` ORM row.

---

## The four provider protocols

```python
@runtime_checkable
class AuthProvider(Protocol):
    async def authenticate_request(self, request: Request) -> AuthIdentity | None: ...

@runtime_checkable
class UserProvider(Protocol):
    async def get_by_id(self, user_id: str, *, request: Request) -> AdminPrincipal | None: ...

@runtime_checkable
class TenantProvider(Protocol):
    async def resolve_tenant(self, request: Request) -> AdminTenant | None: ...

@runtime_checkable
class PermissionProvider(Protocol):
    def is_superadmin(self, user: AdminPrincipal) -> bool: ...
    async def get_permissions(
        self,
        user: AdminPrincipal,
        tenant: AdminTenant | None,
        *,
        request: Request,
    ) -> frozenset[str]: ...
```

Pass implementations to `create_admin()`:

```python
app = create_admin(
    config=cfg,
    auth_provider=MyJWTAuthProvider(),
    user_provider=MyDirectoryUserProvider(),
    permission_provider=MyOpenFGAPermissionProvider(),
    tenant_provider=MyHeaderTenantProvider(),
)
```

Each one defaults to the framework's `Builtin*` implementation if
omitted — so a vanilla v1 app passes none.

---

## `AdminContext` — the one thing routes consume

```python
@dataclass(slots=True)
class AdminContext:
    request: Request | None
    principal: AdminPrincipal | None
    tenant: AdminTenant | None
    permissions: frozenset[str] = field(default_factory=frozenset)
    roles: frozenset[str] = field(default_factory=frozenset)
    source: Literal["ui", "api", "import", "job"] = "api"
    action: str | None = None

    @property
    def is_authenticated(self) -> bool: ...
    @property
    def is_superadmin(self) -> bool: ...
    def has_permission(self, key: str) -> bool: ...
```

Routes inject it via FastAPI:

```python
from adminfoundry.admin import AdminContext, require_admin_context

@router.get("/widgets")
async def list_widgets(ctx: AdminContext = Depends(require_admin_context)):
    if not ctx.has_permission("admin.widgets.list"):
        raise HTTPException(403, "…")
    ...
```

- `require_admin_context` — raises 401 if `principal is None`. Default
  for every CRUD/contract/actions route.
- `build_admin_context` — anonymous-tolerant; use for routes that
  legitimately serve unauthenticated callers (login, health).

---

## What each `Builtin*` does

| Provider | Module | Behaviour |
|---|---|---|
| `BuiltinJWTAuthProvider` | `providers/auth.py` | Validates `Authorization: Bearer <jwt>`, returns `AuthIdentity(user_id, claims=token_payload)`. Anonymous → `None`. |
| `BuiltinSQLAlchemyUserProvider` | `providers/users.py` | Loads `User` from the framework's `User` table, converts to `AdminPrincipal`. Inactive → 403. |
| `BuiltinTenantProvider` | `providers/tenants.py` | Wraps the existing `TenantMiddleware`; reads `request.state.tenant`. Returns `None` for root/public requests. |
| `BuiltinPermissionProvider` | `providers/permissions.py` | Superadmin → `frozenset({"admin.*"})`. Otherwise tenant-scoped lookup via `TenantMembership` → `TenantRole` → `TenantRolePermission` over a `SET LOCAL search_path` (PostgreSQL only). |

PostgreSQL-only caveat: tenant-scoped permission lookup needs
`SET LOCAL search_path`, which SQLite doesn't support. On SQLite, the
built-in provider returns `frozenset()` — the same legacy behaviour the
framework had before v1-providers.

### Boundary: where external auth stops (Roadmap A0.5)

External `user_mode` (your own providers) fully covers **auth, CRUD and
contract**. It does **not** yet cover the superadmin/root tooling
(`root/*`), audit-actor resolution (`audit/service.py`), tenant
bootstrap, or the CLI — those still import the concrete builtin `User`
model. So an external IdP runs the admin surface, but root/audit/CLI
remain builtin-coupled by design for now. This is a deliberate, documented
limitation, not an oversight; full decoupling is tracked in
[stabilization.md](stabilization.md).

---

## Superadmin

A user with `is_superadmin=True` on the `User` row gets
`frozenset({"admin.*"})` from `BuiltinPermissionProvider`.

That key matches every `admin.<resource>.<action>` permission via the
wildcard rule, but **does not** match permissions in other namespaces
(e.g. `oauth.identities.list`). For features whose permission key
isn't under `admin.*`, consumers that want "platform owner sees
everything" must short-circuit on `ctx.is_superadmin` themselves.

The `GET /_navigation` endpoint already does this — see
[extensions.md § NavigationRegistry](extensions.md#navigationregistry--runtimenavigation).

---

## Writing a custom provider

The common case: your app already has an identity system and you want
`adminfoundry` to defer to it instead of hosting its own user table.

```python
from adminfoundry.providers.base import (
    AdminPrincipal, AuthIdentity, AuthProvider, UserProvider,
)


class CompanyAuthProvider:
    """Reads the company SSO cookie and resolves it to a user id."""

    def __init__(self, sso_client):
        self._sso = sso_client

    async def authenticate_request(self, request) -> AuthIdentity | None:
        cookie = request.cookies.get("company_sso")
        if not cookie:
            return None
        user_id = await self._sso.validate(cookie)   # may raise on tampered cookies
        return AuthIdentity(user_id=user_id, claims={"sso": True})


class CompanyDirectoryUserProvider:
    """Loads users from the company directory, not the framework table."""

    def __init__(self, directory):
        self._dir = directory

    async def get_by_id(self, user_id: str, *, request) -> AdminPrincipal | None:
        record = await self._dir.lookup(user_id)
        if record is None:
            return None
        return AdminPrincipal(
            id=record.id,
            email=record.primary_email,
            display_name=record.full_name,
            is_active=record.status == "active",
            is_superadmin="admin" in record.groups,
        )


app = create_admin(
    config=cfg,
    auth_provider=CompanyAuthProvider(sso),
    user_provider=CompanyDirectoryUserProvider(directory),
)
```

Things to know:

1. **`AuthIdentity.user_id`** is whatever opaque string your auth
   provider returns. Your `UserProvider.get_by_id` is the only thing
   that has to know what it means.
2. **Failures are not exceptions.** Return `None` for "no identity" /
   "user not found" — the framework converts that to 401. Raise only
   for genuinely exceptional conditions (signing-key error, DB down).
3. **`request` is passed through** so providers can read tenant
   headers, opt out for health endpoints, or pull request-scoped DB
   sessions. Don't store the `request` between calls — it's per-call.

`tests/providers/test_external_e2e.py` exercises a "no framework User
table at all" scenario as a regression guard.

---

## Optional capabilities: `OAuthCapableUserProvider`

The four core Protocols above answer "who is this principal" (read
path). Some extensions also need a *write* path — the OAuth extension,
for example, may need to find or create a user from a verified
external identity after the IdP redirect lands.

Forcing every `UserProvider` implementation to grow an OAuth-shaped
method they may never use would be wrong. So that capability is its
own opt-in Protocol that lives in the auth_oauth extension:

```python
from adminfoundry.extensions.auth_oauth import OAuthCapableUserProvider


class CompanyOAuthUserProvider:
    async def find_or_create_by_external_identity(
        self, *,
        provider: str,
        provider_subject: str,
        claims,                # ExternalIdentityData
        allow_create: bool,
        request,
    ):
        # Look up the IdP identity in your IAM, return AdminPrincipal,
        # or raise one of the OAuthCapabilityError subclasses to refuse.
        ...


# Wire it into the extension, not into create_admin():
app = create_admin(
    config=cfg,
    extensions=[
        OAuthExtension(
            providers=[GoogleOIDCProvider(client_id=…, client_secret=…)],
            user_provider=CompanyOAuthUserProvider(),  # opt-in
            auto_create_users=False,                   # safe default
        ),
    ],
)
```

The default `BuiltinOAuthUserProvider` operates on the framework's
`User` table and applies four security defaults — see
[auth-oauth.md](auth-oauth.md) for the full setup walkthrough.

This pattern is the model for any future "extension X needs a write
operation on the user store" Protocol: define it in the extension's
own module, ship a `Builtin*` that wraps the framework's `User`,
runtime-checkable so the extension can `isinstance(provider,
ProtocolName)` before using it.

---

## Why not just override `get_current_user`?

The old shape (`get_current_user(request, credentials, session) → User`)
coupled identity, ORM, and the framework's `User` model into a single
dependency. Replacing that dep gave you a hand-wavy promise that "most
things still work" but in practice broke:

- `require_tenant_auth_context` — needed the framework `User`'s `id`.
- `assert_permission(ctx.permission_keys, …)` — needed
  `TenantAuthContext`.
- Audit log `actor_user_id` — needed `request.state.current_user`.
- Impersonation token validation — needed JWT internals.

Splitting the responsibilities across four providers means **none** of
the framework's downstream code reaches into your User model. Routes
read `AdminContext`. That's it.

`get_current_user` itself still lives in `auth/dependencies.py` because
the auth endpoints (`/me`, `/logout-all`) genuinely need the full
`User` row to bump `token_version` — that's not legacy, that's the
single place the JWT primitive belongs.
