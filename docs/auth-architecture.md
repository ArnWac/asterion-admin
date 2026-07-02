# Authentication architecture

asterion separates **identity** (who is calling) from **the framework's view of
that identity** (what it can see), so an application with its own user or auth
system can integrate without forking the package. This document describes the
four providers that make up that seam, the neutral DTOs they exchange, the
`AdminContext` every route consumes, the built-in defaults, and how to write
your own provider.

The whole story in one diagram:

```text
Request → AuthProvider.authenticate_request   → AuthIdentity (user_id + claims)
        → UserProvider.get_by_id              → AdminPrincipal (neutral DTO)
        → TenantProvider.resolve_tenant       → AdminTenant | None
        → PermissionProvider.get_permissions  → frozenset[str]
        ──────────────────────────────────────────────────────────
                              ▼
                  AdminContext(principal, tenant, permissions, …)
                              ▼
                   Every route reads exactly this.
```

The four providers are the **only** seams between asterion and the host
application's identity layer. Routes never look at JWT internals, SQLAlchemy
`User` rows, tenant memberships, or role-permission joins.

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

These live in [`asterion/providers/base.py`](../asterion/providers/base.py). The
framework only ever sees these — never a concrete `User` ORM row.

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
        self, user: AdminPrincipal, tenant: AdminTenant | None, *, request: Request,
    ) -> frozenset[str]: ...
```

Pass implementations to `create_admin()`; each defaults to the framework's
`Builtin*` implementation when omitted, so a vanilla app passes none:

```python
app = create_admin(
    config=cfg,
    auth_provider=MyJWTAuthProvider(),
    user_provider=MyDirectoryUserProvider(),
    permission_provider=MyOpenFGAPermissionProvider(),
    tenant_provider=MyHeaderTenantProvider(),
)
```

## AdminContext — the one thing routes consume

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

Routes inject it through FastAPI:

```python
from asterion.admin import AdminContext, require_admin_context

@router.get("/widgets")
async def list_widgets(ctx: AdminContext = Depends(require_admin_context)):
    if not ctx.has_permission("admin.widgets.list"):
        raise HTTPException(403, "…")
    ...
```

* `require_admin_context` — raises `401` if `principal is None`. The default for
  every CRUD / contract / actions route.
* `build_admin_context` — anonymous-tolerant; use for routes that legitimately
  serve unauthenticated callers (login, health).

## The built-in providers

| Provider | Module | Behaviour |
|---|---|---|
| `BuiltinJWTAuthProvider` | `providers/auth.py` | Validates `Authorization: Bearer <jwt>`; returns `AuthIdentity(user_id, claims=token_payload)`. Anonymous → `None`. |
| `BuiltinSQLAlchemyUserProvider` | `providers/users.py` | Loads `User` from the framework's table, converts to `AdminPrincipal`. Inactive → `403`. |
| `BuiltinTenantProvider` | `providers/tenants.py` | Wraps `TenantMiddleware`; reads `request.state.tenant`. Root/public requests → `None`. |
| `BuiltinPermissionProvider` | `providers/permissions.py` | Superadmin → `frozenset({"admin.*", "platform.*"})`. No tenant → the caller's `platform.*` keys from their `PlatformRole`s (public-schema lookup, any backend). Tenant + non-superadmin → tenant-scoped lookup via `TenantMembership` → `TenantRole` → `TenantRolePermission` over a `SET LOCAL search_path` (PostgreSQL only). |

> **PostgreSQL-only caveat.** The *tenant-scoped* lookup needs
> `SET LOCAL search_path`, which SQLite doesn't support — on SQLite it returns
> `frozenset()`. The *platform* lookup reads public-schema tables and works on
> any backend.

### Where external auth stops

External `user_mode` (your own providers) fully covers **auth, CRUD, and
contract**. It does **not** yet cover the superadmin/root tooling (`root/*`),
audit-actor resolution (`audit/service.py`), tenant bootstrap, or the CLI —
those still import the concrete built-in `User`. So an external IdP runs the
admin surface, but root/audit/CLI remain built-in-coupled by design for now.

## Platform tier & superadmin

Authority splits into two tiers ([ADR-0004](adr/0004-platform-tier-rbac.md)),
both expressed as permission keys so every gate is a `has_permission` check:

- A user with `is_superadmin=True` gets `frozenset({"admin.*", "platform.*"})`.
  `admin.*` matches every tenant-tier `admin.<resource>.<action>`; `platform.*`
  is the god-mode grant for `superadmin_only` global resources. `is_superadmin`
  is **CLI-only** (not settable through `UserAdmin`), since `platform.*` is
  minted from it.
- **Platform staff** hold a scoped subset of `platform.*` via a `PlatformRole`
  (public schema, superadmin-administered, linked by `PlatformUserRole`) — the
  graded shared-scope operator role. Tenant owners can never receive
  `platform.*` (it is excluded from tenant seeding).

Neither wildcard matches other namespaces (e.g. `oauth.identities.list`), so the
`GET /_navigation` endpoint still short-circuits on the `platform.*` grant to let
a full operator see every registered item — see
[Extensions § NavigationRegistry](extensions.md#navigationregistry). A scoped
staff grant does **not** trigger that bypass.

## Service / machine accounts

A device or service-to-service caller (e.g. a stationary time-clock terminal)
needs an account that authenticates **only** via a minted access token, never a
password. `asterion.auth.service_accounts.create_service_account` provisions one
in a single call instead of hand-assembling `User` + `TenantMembership` + RBAC:

```python
from asterion.auth.service_accounts import create_service_account
from asterion.auth.tokens import create_access_token

# `session` MUST be tenant-scoped (SET LOCAL search_path), like get_async_session.
user = await create_service_account(
    session,
    tenant_id=tenant.id,
    label="lobby-terminal",
    permission_keys=["admin.time_entries.create"],
)
token = create_access_token(
    user.id,
    secret_key=config.secret_key,
    algorithm=config.jwt_algorithm,
    expires_minutes=config.access_token_expire_minutes,
    token_version=user.token_version,
)
```

It creates an **active, passwordless** user (`is_active=True`,
`is_superadmin=False`, `is_service_account=True`, with an unusable password hash
so `POST /auth/login` rejects it), a `TenantMembership`, and a dedicated
`service:<label>` role granting the permission keys. It does **not** mint tokens
— that is the caller's job. A token for this account resolves through the normal
tenant-RBAC path to a principal carrying exactly those keys.

The `is_service_account` flag makes the account a first-class type: it is
excluded from the password-reset flow (so a reset can't turn a token-only
account into a login-capable one) and is identifiable in queries / UI.

**Revocation** is the standard per-user invariant: bump `user.token_version` or
set `user.is_active = False`. To tear an account down entirely (user + membership
+ dedicated role), use `delete_service_account(session, user_id=…, tenant_id=…)`.

The CLI wraps both helpers (and prints one freshly minted token on create):

```bash
asterion service-account create --tenant acme --label lobby-terminal \
    --permission admin.time_entries.create --permission admin.time_entries.read
asterion service-account delete --tenant acme --email <account-email>
```

## Writing a custom provider

The common case: your app already has an identity system and you want asterion
to defer to it instead of hosting its own user table.

```python
from asterion.providers.base import (
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

Three things to know:

1. **`AuthIdentity.user_id` is opaque.** It is whatever string your auth
   provider returns; your `UserProvider.get_by_id` is the only thing that needs
   to know what it means.
2. **Failures are not exceptions.** Return `None` for "no identity" / "user not
   found" — the framework converts that to `401`. Raise only for genuinely
   exceptional conditions (signing-key error, DB down).
3. **`request` is passed through** so providers can read tenant headers, opt out
   for health endpoints, or pull a request-scoped DB session. Don't store the
   `request` between calls — it is per-call.

`tests/providers/test_external_e2e.py` exercises a "no framework `User` table at
all" scenario as a regression guard.

## Optional capabilities: OAuthCapableUserProvider

The four core Protocols answer "who is this principal" (the read path). Some
extensions also need a *write* path — the OAuth extension, for example, may need
to find or create a user from a verified external identity after the IdP
redirect. Forcing every `UserProvider` to grow an OAuth-shaped method it may
never use would be wrong, so that capability is its own opt-in Protocol that
lives in the `auth_oauth` extension:

```python
from asterion.extensions.auth_oauth import OAuthCapableUserProvider


class CompanyOAuthUserProvider:
    async def find_or_create_by_external_identity(
        self, *, provider, provider_subject, claims, allow_create, request,
    ):
        # Look up the IdP identity, return AdminPrincipal, or raise an
        # OAuthCapabilityError subclass to refuse.
        ...


app = create_admin(
    config=cfg,
    extensions=[
        OAuthExtension(
            providers=[GoogleOIDCProvider(client_id=…, client_secret=…)],
            user_provider=CompanyOAuthUserProvider(),   # opt-in
            auto_create_users=False,                    # safe default
        ),
    ],
)
```

The default `BuiltinOAuthUserProvider` operates on the framework's `User` table
and applies four security defaults — see [OAuth / OIDC sign-in](auth-oauth.md).
This pattern is the model for any future "extension X needs a write operation on
the user store" Protocol: define it in the extension's own module, ship a
`Builtin*` that wraps the framework's `User`, and make it runtime-checkable so
the extension can `isinstance`-check before using it.

## Background: why four providers

The earlier shape — `get_current_user(request, credentials, session) → User` —
coupled identity, ORM, and the framework's `User` model into a single
dependency, so swapping it broke `require_tenant_auth_context`,
`assert_permission`, audit `actor_user_id`, and impersonation token validation.
Splitting the responsibilities across four providers means none of the
framework's downstream code reaches into your `User` model — routes read
`AdminContext`, and that is it. `get_current_user` still lives in
`auth/dependencies.py` because the auth endpoints (`/me`, `/logout-all`)
genuinely need the full `User` row to bump `token_version` — the single place
that JWT primitive belongs.

## See also

* [Security](security.md) — token claims, revocation, authorization.
* [Extensions](extensions.md) — provider-vs-extension decision, OAuth wiring.
* [Multi-tenancy](tenancy.md) — how the tenant + permission providers resolve.
