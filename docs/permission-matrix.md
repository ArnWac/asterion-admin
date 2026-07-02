# Permission matrix

The default role → permission-key mapping, how keys are shaped, and how the
catalog is generated. Source of truth in code:
[`asterion/authz/catalog.py`](../asterion/authz/catalog.py) and
[`asterion/tenancy/bootstrap.py`](../asterion/tenancy/bootstrap.py).

## Permission keys

A key is `<namespace>.<resource>.<action>`. Wildcards are allowed **only** at the
trailing segment (`admin.*`, `admin.posts.*`, `platform.*`); middle wildcards are
rejected on parse. The matcher is wildcard-aware — see
[security.md](security.md#authorization).

Two namespaces, two tiers ([ADR-0004](adr/0004-platform-tier-rbac.md)):

- **`admin.*`** — the **tenant tier**: per-resource keys assignable to tenant
  roles by a tenant owner.
- **`platform.*`** — the **platform tier**: keys for `superadmin_only` global
  resources, assignable only to `PlatformRole`s by a superadmin, **never** to a
  tenant role. A superadmin holds both `admin.*` and `platform.*`.

For every registered resource the catalog generates the five default CRUD
actions plus one key per declared custom admin action, in the namespace that
matches the admin's tier (`platform` for `superadmin_only`, else `admin`):

```
admin.<resource>.{list, read, create, update, delete}      # tenant-tier resource
admin.<resource>.<custom_action>                            # per @action
platform.<resource>.{list, read, …}                        # superadmin_only resource
```

Extensions contribute their own namespaced keys (e.g.
`oauth.identities.list`). The catalog is populated via
`asterion permissions sync`; tenant bootstrap seeds roles from it, **excluding
`platform.*` keys** so a tenant role can never be granted platform authority.

## Default tenant roles

Three system roles are seeded per tenant
([`bootstrap.py`](../asterion/tenancy/bootstrap.py), `_DEFAULT_ROLE_DEFS`):

| Role | Grants | Rule |
|---|---|---|
| **owner** | `admin.*` **+ every catalog key** | Full tenant access. Always at least `admin.*`. |
| **admin** | Every catalog key **except** the deny list | `_ADMIN_PERMISSIONS_DENY` = `admin.audit_logs.delete`, `admin.users.delete` |
| **viewer** | Every catalog key ending in `.list` | Read-only (list) access. |

So out of the box:

| Action (example resource `posts`) | owner | admin | viewer |
|---|:--:|:--:|:--:|
| `admin.posts.list` | ✅ | ✅ | ✅ |
| `admin.posts.read` | ✅ | ✅ | ❌ |
| `admin.posts.create` | ✅ | ✅ | ❌ |
| `admin.posts.update` | ✅ | ✅ | ❌ |
| `admin.posts.delete` | ✅ | ✅ | ❌ |
| `admin.audit_logs.delete` | ✅ | ❌ (denied) | ❌ |
| `admin.users.delete` | ✅ | ❌ (denied) | ❌ |

Seeding is **idempotent** — re-running bootstrap only adds missing rows. Custom
roles are created and granted through the tenant RBAC UI / API.

## Platform (global) scope

Global resources — `users`, `tenants`, `audit_logs`, `impersonation_logs`,
`platform_roles` — are **not** governed by tenant roles. They are the
**platform tier** ([ADR-0004](adr/0004-platform-tier-rbac.md)): reachable only
by a caller holding the matching `platform.<resource>.<action>` key.

- A **superadmin** holds `platform.*` (mapped from `User.is_superadmin`) and can
  do everything. `is_superadmin` is settable **only via the CLI**
  (`asterion user …`), never through the admin UI — so one superadmin cannot
  mint another with a click.
- **Platform staff** get graded access through a **`PlatformRole`** (public
  schema, superadmin-administered) holding a subset of `platform.*` keys, linked
  directly to a user via `PlatformUserRole`. This is the least-privilege global
  "Support" role (formerly roadmap G14), managed through `PlatformRoleAdmin`.

Root routes (impersonation, cross-tenant tooling) remain **full-superadmin
identity** gates — a scoped staff grant does not reach them; impersonation
tokens are rejected there.

With no tenant context (single-tenant / shared scope) the caller's `platform.*`
keys authorize them; a caller with none falls back to
`single_tenant_require_superadmin` (default `True`), i.e. superadmin required.

## Regenerating this matrix

The role→key seeding is data-driven from the catalog, so the concrete keys for
*your* deployment depend on your registered resources. Inspect them with:

```bash
asterion permissions sync     # populate / refresh PermissionCatalog
asterion permissions list     # list catalog keys (resource × action)
```

## See also

- [security.md — Authorization](security.md#authorization)
- [tenancy.md](tenancy.md) — tenant RBAC tables and resolution.
- [model-admin.md](model-admin.md) — declaring resources, actions, policies.
