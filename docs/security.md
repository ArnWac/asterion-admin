# Security

This document describes asterion's security model: how callers are
authenticated, how requests are authorized, how inputs are validated, how
sensitive fields are protected and secrets sanitized, what is audited, and the
known limitations you must account for in a production deployment.

## Authentication

Authentication uses JWT bearer tokens. Two token types share the same signing
key but carry a `type` claim and are validated differently:

| Token type | Used for | Allowed at superadmin routes? |
|---|---|---|
| `access` | Normal user requests | Yes (if `is_superadmin=True`) |
| `impersonation` | A superadmin acting as another user | **No** — `require_superadmin` rejects them |

### Token claims

```text
sub               user id (the IMPERSONATED user for impersonation tokens)
tkv               user.token_version at issue time
type              "access" | "impersonation"
jti               per-token uuid, linked to ImpersonationLog rows
exp               expiry timestamp
iat               issued-at
impersonated_by   superadmin user id (impersonation only)
tenant_id         optional tenant scope (impersonation only)
```

### Revocation

The primary mechanism is **user-wide** via `User.token_version`. Increment the
column and every previously issued token for that user fails its `tkv` check on
the next request (this is what "log out everywhere" does).

Single-token revocation (a `RevokedToken` table keyed by `jti`) is intentionally
out of scope; add it if per-session logout becomes a requirement.

### Login rate limiting

`InMemoryLoginRateLimiter` blocks a given email after a threshold of failed
login attempts. It is **not distributed**: under `uvicorn --workers N` the
effective limit becomes `N × threshold`. For multi-worker production, wire a
shared backend that satisfies the `RateLimiterBackend` Protocol — the bundled
`asterion.extensions.rate_limit_redis` provides one.

## Authorization

Authorization is by **permission key**: `admin.<resource>.<action>`. Wildcards
are allowed **only** at the trailing segment.

| Granted on a role | Required by an endpoint | Match? |
|---|---|---|
| `admin.*` | `admin.posts.list` | Yes |
| `admin.posts.*` | `admin.posts.delete` | Yes |
| `admin.posts.delete` | `admin.posts.delete` | Yes |
| `admin.posts.list` | `admin.posts.delete` | No → `403` |
| `admin.*.list` | anything | **Rejected on parse** — middle wildcards are illegal |

The CRUD router computes the required key per endpoint and calls
`AdminContext.has_permission(required)`, which delegates to the wildcard-aware
matcher.

### Single-tenant / no-tenant scope

Permission keys are a *tenant-role* concept. With no tenant context
(single-tenant deployments, or root scope) there is no role system to gate by,
so the admin surface (CRUD, actions, import/export) requires a **superadmin** by
default — otherwise any authenticated, active account could manage everything.
This is controlled by `CoreAdminConfig.single_tenant_require_superadmin`
(default `True`); set it `False` only if you deliberately want every
authenticated caller to have full access.

> **Revocation note.** Clearing `is_superadmin` is re-evaluated on the next
> request, but it does **not** invalidate an already-issued JWT — the token
> stays valid until it expires. To cut existing sessions immediately, bump the
> user's `token_version` (log out everywhere) or set `is_active=False` (rejected
> on the next request).

## Input validation

Every external identifier passes through `asterion.security.validation`:

```python
validate_resource_name(value)            # ^[a-z][a-z0-9_-]{0,62}$
validate_action_name(value)              # ^[a-z][a-z0-9_]{0,62}$   (no hyphens)
validate_tenant_slug(value)              # ^[a-z][a-z0-9-]{1,62}$
validate_schema_name(value)              # ^[a-z][a-z0-9_]{0,62}$ + reserved-name guard
validate_permission_key(value)           # ns.resource.action with trailing-* only
validate_limit_offset(limit=, offset=)   # bounded [1, 500]; offsets clamped to 0
```

The resource / action / key validators are invoked from the registry, the CRUD
router, the actions router, the contract router, the permission-sync flow, and
`permission_key()`. Tenant slugs and schema names are validated at tenant
create + bootstrap time. Pagination is normalized in
`crud.query.normalize_limit_offset`.

## Field protection

`ModelAdmin` exposes two field-protection knobs (see also
[ModelAdmin reference](model-admin.md#modeladminprotected_fields)):

```python
class WidgetAdmin(ModelAdmin):
    readonly_fields  = ["id", "created_at", "updated_at"]   # 422 on mutation
    protected_fields = ["internal_token"]                   # never serialized, never accepted
```

On top of those, the framework-wide `ProtectedFieldRegistry` (seeded from
`DEFAULT_PROTECTED_FIELDS`; extensions add more via `register_protected_fields`)
always masks `hashed_password`, `password`, `totp_secret`, `secret`, OAuth
tokens, and similar — no matter what a per-admin config says. These rules apply
uniformly to:

* the contract (`GET /api/v1/admin/_contract/{resource}` drops hidden + globally
  protected fields);
* serializer output (list and detail responses omit them);
* the payload validator (`clean_write_payload` rejects writes with `422`).

### One resolution rule

A field's effective permission has three *inputs* — `protected_fields`
(→ `HIDDEN`), `readonly_fields` (→ `READ`), and the per-user, per-row
`AdminPolicy.field_permission()` — but **one** resolver combines them:
`FieldPermission.strictest(...)`, where `WRITE < READ < HIDDEN`. The static
class (`static_field_permission`) is computed from the admin's lists, then the
policy result is folded in with `strictest`.

The invariant: **a policy can only ever tighten, never loosen.** A field that is
statically `READ` or `HIDDEN` can never be widened to `WRITE` by a policy. The
three knobs are ergonomic shortcuts, not competing mechanisms — they are inputs
to the single `strictest` rule. See
[`asterion/admin/policy.py`](../asterion/admin/policy.py), tested in
`tests/crud/test_field_permission_resolution.py`.

## Secret sanitization

`asterion.security.sanitize.sanitize_payload(payload)` recursively walks a
dict/list and redacts values under keys that contain (with word-boundary
matching) any of:

```text
password, hashed_password, password_hash, new_password, current_password,
token, access_token, refresh_token, secret, secret_key,
authorization, cookie, set_cookie, api_key, apikey, private_key
```

Audit `changes` columns and log records pass through this before they reach the
database or log handler. Word-boundary matching means `tokens` (plural) is not a
sensitive key, but `access_token` is.

## Audit

Every login, CRUD write, admin action, and impersonation start writes one
`AuditLog` row. Rows are written in one of two modes:

* **In-session, savepoint-isolated** (`record_audit_in_session`) for CRUD and
  actions. The row commits with the main transaction; an audit-insert failure is
  caught and never breaks the response.
* **Isolated session** (`record_audit`) for login (which re-raises after audit
  on failure paths — an in-session row would otherwise roll back).

Audit values pass through the sanitizer before insert. There is no automatic
retention — see [Operating notes](deployment.md#operating-notes) for the cleanup
query.

## Error envelope

Every error response uses one shape, so clients can rely on `error.code` and
`error.message`. See [Architecture](architecture.md) for the canonical codes and
[`asterion/core/errors.py`](../asterion/core/errors.py) for raising custom
envelopes via `AdminError`.

## Known limitations

Be explicit about what the framework does and does not guarantee:

* **PostgreSQL schema isolation is only *proven* when `pytest -m postgres` runs
  in CI** against a real PostgreSQL service — SQLite tests cannot verify it. (It
  does run in CI; see `.github/workflows/ci.yml`.)
* **The in-memory login rate limiter is not distributed** (per process, keyed by
  email only). For multi-worker production, wire a shared backend
  (`asterion.extensions.rate_limit_redis`). `(email, ip)` keying is a planned
  follow-up that builds on `trusted_proxy_count`.
* **Root / audit / CLI are coupled to the built-in `User` model.** External
  `user_mode` (your own providers) fully covers auth + CRUD + contract, but
  `root/*`, `audit/service.py`, `tenancy/bootstrap.py`, and `cli/main.py` still
  import the concrete built-in `User`. An external IdP runs the admin surface;
  superadmin/root tooling and audit-actor resolution remain built-in by design
  for now.
* **No Content-Security-Policy by default** (the bundled UI's inline config
  scripts would break a strict `script-src 'self'`). Set
  `CoreAdminConfig.content_security_policy` (or
  `ASTERION_CONTENT_SECURITY_POLICY`) to emit one — recommended for API-first
  deployments with their own frontend, e.g.
  `default-src 'self'; frame-ancestors 'none'`. The built-in UI keeps the access
  token in `localStorage`, so a strict CSP is the main defence against token
  theft via XSS.
* **Client IP ignores `X-Forwarded-For` by default** (tenant IP allowlist +
  audit `ip_address` see the direct peer). Behind a reverse proxy, set
  `CoreAdminConfig.trusted_proxy_count` (or `ASTERION_TRUSTED_PROXY_COUNT`) to
  the number of trusted hops and run `uvicorn --proxy-headers`. Never set it
  above the real hop count — that would let clients spoof the IP via the header.
* **No automatic audit retention.** Schedule
  `DELETE FROM audit_logs WHERE created_at < NOW() - INTERVAL '90 days';`.

## See also

* [Auth architecture](auth-architecture.md) — providers, DTOs, custom identity.
* [Multi-tenancy](tenancy.md) — schema isolation and tenant RBAC.
* [Service accounts](auth-architecture.md#service--machine-accounts) — token-only
  machine callers.
