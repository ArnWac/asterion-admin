# adminfoundry v1 — Provider Refactor Roadmap

## Status

Active. Branch: `v1-providers`. Motivation: real Google OAuth integration
upcoming, which the current built-in JWT/User stack cannot support without
forking the framework. The "no speculative building" principle from
[adminfoundry_v1_core_extensions_roadmap.md](adminfoundry_v1_core_extensions_roadmap.md)
is **explicitly suspended for this initiative** because the use case is
now concrete.

## Goal

Decouple `adminfoundry` from any specific Auth / User / Permission /
Tenant implementation. After the refactor:

- The built-in JWT + SQLAlchemy User stack continues to work unchanged
  (default for quickstart). It is implemented as the default Provider
  set and is no longer privileged in the code.
- New apps can plug in `auth_provider=GoogleOAuthProvider()` and a
  matching `user_provider` to bring their own identity layer.
- Routers, services, contract builder, and audit hooks operate against
  a neutral `AdminContext` and never import concrete model classes for
  the User, Tenant, or Permission concept.

Driving design document:
[adminfoundry_admin_package_gap_analysis.md](adminfoundry_admin_package_gap_analysis.md).

---

## Phased Plan

Each phase ends green (ruff + full pytest pass). Each phase is mergeable
on its own. If the project ships before Phase 6, what's there is still
useful — the foundation lives alongside the existing code rather than
replacing it.

### Phase 1 — Foundation (this session)

Add the new abstractions next to the existing code. **No existing code
path changes.** Default providers wrap current implementations 1:1.

Deliverables:
- `adminfoundry/providers/base.py` — `AuthProvider`, `UserProvider`,
  `PermissionProvider`, `TenantProvider` protocols + `AdminUser`,
  `AuthIdentity`, `AdminTenant` DTOs.
- `adminfoundry/providers/auth.py` — `BuiltinJWTAuthProvider` wrapping
  current token-decode logic.
- `adminfoundry/providers/users.py` — `BuiltinSQLAlchemyUserProvider`
  wrapping the `User` model lookup.
- `adminfoundry/providers/permissions.py` —
  `BuiltinPermissionProvider` delegating to the tenant role lookup.
- `adminfoundry/providers/tenants.py` — `BuiltinTenantProvider`
  wrapping the current tenant resolver.
- `adminfoundry/admin/context.py` — `AdminContext` dataclass +
  `build_admin_context()` FastAPI dependency.
- Extended `AdminRuntime` to hold the four providers.
- `create_admin(..., auth_provider=, user_provider=, permission_provider=,
  tenant_provider=)` — optional kwargs, fall back to builtin defaults.
- Legacy-guard package allowlist update for `providers/`.
- Unit tests for each provider's default behaviour.
- End-to-end test that wires fake external providers through
  `create_admin` and verifies the `AdminContext` is built from them.

What stays unchanged in Phase 1:
- Every existing router, dependency, service, and test.
- `get_current_user`, `require_superadmin`, `require_tenant_auth_context`,
  `TenantMiddleware` — all keep working.

### Phase 2 — AdminContext threading through CRUD

Migrate the CRUD router (`adminfoundry/crud/router.py`) and the contract
router to accept `ctx: AdminContext = Depends(build_admin_context)`
alongside the existing dependencies. Default ctx is built from the same
data as `get_current_user` + `require_tenant_auth_context`, so behaviour
is identical. New code paths can rely on `ctx.user`/`ctx.tenant` without
importing `User`/`Tenant`.

Tests: parametrize a subset of CRUD tests over both the legacy path and
the ctx path to prove equivalence.

### Phase 3 — Permission Provider consumption

Replace `_require_resource_permission(auth, …)` with
`runtime.permissions.assert_can(ctx, action, resource)`. The default
provider preserves wildcard semantics and the existing deny list.
External providers can implement arbitrary logic (RBAC, ABAC, OPA,
whatever).

### Phase 4 — Tenant Provider consumption

Replace `TenantMiddleware` and `request.state.tenant` with
`runtime.tenants.resolve_tenant(request)`. The default wraps the current
header/subdomain resolver. External providers can pull tenant info from
JWT claims, session cookies, or anywhere.

### Phase 5 — Google OAuth provider + example app

New file: `examples/external_auth_google/` — full demo of an
adminfoundry app whose auth_provider is `GoogleOAuthProvider`, whose
user_provider reads from the app's existing `MyUser` table, whose
permission_provider is RBAC against the app's own role table. **This is
the validation step** — if Phase 1–4 are right, this example needs
zero framework changes.

Dependencies introduced (optional extras only):
- `authlib` or `httpx` for OAuth2 flow

### Phase 6 — Cleanup + deprecation path

- Mark `get_current_user`, `require_tenant_auth_context`, and the
  concrete-User-importing helpers as deprecated. Provide a one-version
  shim so external apps using them still work.
- Update `docs/architecture.md`, `docs/security.md`, `docs/tenancy.md`
  to describe the provider model as the primary integration path.
- Update `examples/basic_single` and `examples/multi_tenant` to use
  `create_admin(..., providers...)` with the builtin set — proves the
  defaults are still ergonomic.

---

## Out of scope for this initiative

These items from the gap analysis are independent of providers and
should stay on their own roadmap (or v2):

- `InlineAdmin` (P1)
- Field Registry / FieldAdapter (P0 #3 in gap analysis)
- Contract versioning (P0 #4 — small, can be done independently)
- Lifecycle hooks (P1)
- Policy abstraction beyond `PermissionProvider` (P0 #5)
- Form layout API / Fieldsets (P1)
- List-view saved filters etc.

Reason for exclusion: the provider refactor is already large. Bundling
unrelated features doubles the surface area and slows the time-to-merge.

---

## Acceptance Criteria

- `create_admin(config=...)` works with built-in defaults — no
  behavioural change vs. main.
- `create_admin(config=..., auth_provider=..., user_provider=...)` works,
  end-to-end, with non-trivial external providers.
- A route handler can access `ctx.user` without importing
  `adminfoundry.models.user.User`.
- Permission checks go through `PermissionProvider` interface — the
  default preserves all existing semantics.
- Full test suite green at every phase boundary.
- Phase 5 ships a working Google OAuth example app.

---

## Risks

- **Half-finished refactor** — if Phase 2 starts but Phase 3 doesn't
  finish, the codebase has two parallel auth paths. Mitigation: each
  phase is mergeable independently and the default providers ensure
  no behavioural regression.
- **External-provider edge cases** — Google OAuth has token refresh,
  PKCE, state-cookie semantics that don't map cleanly to a single
  `authenticate_request()` call. Mitigation: defer cookie/state plumbing
  to the example app's responsibility; the protocol stays narrow.
- **Permission semantics drift** — the current wildcard matcher
  (`admin.*`, `admin.foo.*`) is non-trivial. The default
  `PermissionProvider` must reproduce it bit-for-bit. Mitigation: pull
  the matcher into a pure function `match_permission(granted, required)`
  and reuse it from both old and new code paths.
