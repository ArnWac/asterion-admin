# ADR-0004 — Platform authority as a second RBAC tier, not a superadmin boolean

**Status:** Accepted

## Context

Authorization in asterion runs on **permission keys** (`admin.<resource>.<action>`)
resolved by a swappable `PermissionProvider` and consumed uniformly as
`ctx.has_permission(key)`. That is the intended single mechanism.

Alongside it lived a **second, parallel axis**: the global `is_superadmin`
boolean on `public.users`, checked ad-hoc in ~6 decision sites (the CRUD
`superadmin_only` gate, the single-tenant gate, the root-route dependency, the
navigation bypass, and `SuperadminDeletablePolicy`'s delete gate). The boolean
existed because the default provider mapped a superadmin to `admin.*` — the
**same** grant a tenant `owner` holds. The two were indistinguishable at the
key level, so any rule that had to let the platform operator through while
keeping every tenant role (owner included) out had no choice but to branch on
`is_superadmin` directly. `SuperadminDeletablePolicy` was the canonical symptom:
a bespoke policy whose delete gate read the boolean instead of a key.

Two forces made this untenable:

1. **A key in the tenant-assignable catalog is mintable by a tenant owner.**
   Owners manage their tenant's roles (`TenantRolePermission`). Expressing
   platform authority as an ordinary catalog key would let an owner grant it to
   their own role — a cross-tenant privilege escalation. The `is_superadmin`
   gate was, in effect, encoding a real isolation invariant: *platform authority
   must not be mintable by a tenant.*
2. **Operators need graded staff, not a single god flag.** Running the product
   means letting your own employees administer it at the shared (no-tenant)
   scope with *limited* rights (support reads tenants, billing touches
   invoices), not handing everyone full `is_superadmin`. A boolean cannot
   express a subset.

## Decision

Model platform authority as a **second RBAC tier, symmetric to the tenant tier
but separated by which authority administers it**:

| | Tenant tier | Platform tier |
|---|---|---|
| Namespace | `admin.*` | `platform.*` |
| Role store | tenant schema (`TenantRole`) | **public schema (`PlatformRole`)** |
| Administered by | tenant owner (self-service) | superadmin |
| Login scope | inside a tenant | shared / no-tenant |
| Decision | `has_permission("admin.…")` | `has_permission("platform.…")` |

- **One mechanism.** Every authorization *decision* becomes
  `has_permission(...)`. No router or policy reads `is_superadmin`.
  `SuperadminDeletablePolicy` becomes an ordinary key-gated policy
  (`has_permission("platform.<resource>.delete")`); the special case dissolves.
  `superadmin_only` becomes "this admin authorizes against the `platform.*`
  namespace" rather than a boolean short-circuit.
- **The `PermissionProvider` is the one place identity becomes keys.** A
  superadmin maps to `{"admin.*", "platform.*"}` (keeps `admin.*` so they can
  still act inside any tenant). A non-superadmin at shared scope resolves to the
  `platform.*` subset carried by their `PlatformRole`s.
- **Isolation invariant, preserved by store separation.** `platform.*` keys are
  assignable **only** through the public-schema `PlatformRole` store, which only
  a superadmin administers — never through tenant RBAC. They are excluded from
  the tenant-assignable catalog. A tenant owner therefore still cannot mint
  platform authority.
- **`is_superadmin` stays as identity, CLI-only.** It remains the "holds
  `platform.*`" god-mode shorthand, but is settable **only via the CLI**, never
  through the admin UI — even a superadmin cannot mint another superadmin
  without host shell access. It is removed from `UserAdmin`'s writable surface.
  It continues to flip to `False` during impersonation.

This is the standard "staff console vs. tenant admin" split of B2B multi-tenant
SaaS (cf. Django's `is_staff` + granular permissions + `is_superuser`), refined
so the two permission stores are separated by administering authority rather
than collapsed into one namespace.

## Consequences

- **Positive:** one authorization channel (`has_permission`) end to end; the
  `is_superadmin` special-casing and `SuperadminDeletablePolicy`'s bespoke logic
  disappear; graded platform staff become expressible; the "who is a platform
  operator" decision is app-customizable at the swappable `PermissionProvider`,
  the correct layer.
- **Negative:** a new public-schema RBAC store (`PlatformRole`,
  `PlatformRolePermission`, `PlatformUserRole`) plus migration and a
  superadmin-only management admin; two catalogs (tenant-assignable vs.
  platform-assignable) that must not bleed into each other.
- **Isolation kept:** because `platform.*` lives in a store only superadmins
  administer and is absent from the tenant catalog, a tenant owner cannot grant
  it — the [schema-per-tenant](0001-schema-per-tenant.md) boundary is not
  weakened.

See [security.md](../security.md), [permission-matrix.md](../permission-matrix.md),
and [auth-architecture.md](../auth-architecture.md).
