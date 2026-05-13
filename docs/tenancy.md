# Tenancy

## Overview

Multi-tenancy is opt-in (`MULTI_TENANT=true`). When disabled, all tenant logic is bypassed and the app behaves as a single-tenant system.

## TenantContext

`adminfoundry.tenancy.TenantContext` is a dataclass attached to `request.state.tenant` by `TenantMiddleware`. It is always a `TenantContext` or `None` — never the raw ORM `Tenant` object.

```python
@dataclass
class TenantContext:
    id: uuid.UUID
    slug: str
    name: str
    is_active: bool
    schema_name: str          # f"tenant_{slug}" — validated, injection-safe
    timezone: str | None
    language: str | None
    date_format: str | None
    date_pattern: str | None
    allowed_cidrs: str | None
    is_superadmin_context: bool = False
```

## Resolution Paths

### Subdomain strategy (`TENANT_RESOLUTION_STRATEGY=subdomain`)

```
Host: acme.yourdomain.com  →  slug = "acme"
```

The first subdomain part is extracted. RESERVED_SLUGS (`api`, `admin`, `www`, etc.) are skipped to avoid accidental resolution.

### Header strategy (default)

```
X-Tenant-Slug: acme  →  slug = "acme"
```

## Caching

`resolve_tenant()` in `tenancy/resolver.py` caches lookups for 30 s:

1. Redis (`tenant:<slug>` key, SETEX 30 s) — when a Redis client is available.
2. In-memory dict fallback (monotonic expiry, 30 s TTL).

Cache is invalidated via `clear_tenant_cache()`. The conftest fixture calls this automatically between tests.

## TenantMiddleware

`adminfoundry.tenancy.TenantMiddleware` (also re-exported from `adminfoundry.middleware.tenant`):

1. If `MULTI_TENANT=False` — skips all logic.
2. Extracts slug via resolution strategy.
3. If no slug → `request.state.tenant = None`, proceed.
4. If slug → `resolve_tenant()` → `TenantContext | None`.
5. If `None` (slug not found in DB) → 404.
6. If `is_active=False` → 403.
7. If `allowed_cidrs` set → CIDR check → 403 on mismatch.
8. Sets `request.state.tenant = ctx`.

## Schema Name Validation

`SchemaTenantStrategy` in `tenancy/schema_strategy.py` validates schema names before raw SQL interpolation:

```python
_SAFE_SCHEMA_RE = re.compile(r"^tenant_[a-z0-9_]+$")
```

`Tenant.schema_name` always returns `f"tenant_{slug}"`. Slugs are validated at creation time via `RESERVED_SLUGS` and database-level uniqueness. The regex adds a defense-in-depth check in the engine layer against any path that bypasses slug validation.

## SchemaTenantStrategy

Per-tenant PostgreSQL engine cache in `tenancy/schema_strategy.py`:

- `get_or_create_tenant_engine(schema_name)` — returns a cached engine with `SET search_path TO <schema_name>, public` wired on every connection.
- `get_tenant_session(schema_name)` — yields an `AsyncSession` for that engine.
- On SQLite (tests), the shared engine is reused (no schema support).

## Superadmin / Impersonation

- Superadmin without a tenant context sees the global root panel.
- Superadmin can impersonate a tenant by using an impersonation token (issued via `POST /api/v1/tenants/{id}/impersonate`).
- Impersonation tokens carry `tenant_id` and `impersonated_by` in the JWT payload.
- `resolve_impersonation_tenant()` (in `tenancy/resolver.py`) resolves the tenant from the token when `TenantMiddleware` didn't set one (same-origin mode — no subdomain).

## Known Inconsistency / Follow-up

- `CoreAdminConfig` class name uses the old `core` prefix. Renaming it is a separate cleanup task.
- `is_superadmin_context` on `TenantContext` is set to `False` by default. Setting it to `True` for the root panel is a follow-up task.
