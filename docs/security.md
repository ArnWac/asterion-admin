# Security model

## Authentication

JWT bearer tokens. Two token types share the same signing key but carry a
`type` claim and are validated differently:

| Token type | Used for | Allowed at superadmin routes? |
|---|---|---|
| `access` | Normal user requests | yes (if `is_superadmin=True`) |
| `impersonation` | Superadmin acting as another user | **no** — `require_superadmin` rejects them |

### Token claims

```text
sub   user id (the IMPERSONATED user for impersonation tokens)
tkv   user.token_version at issue time
type  "access" | "impersonation"
jti   per-token uuid, linked to ImpersonationLog rows
exp   expiry timestamp
iat   issued-at
impersonated_by   superadmin user id (impersonation only)
tenant_id          optional tenant scope (impersonation only)
```

### Revocation

The MVP mechanism is **user-wide** via `User.token_version`. Increment the
column and every previously issued token for that user fails its `tkv`
check on the next request.

Single-token revocation (a `RevokedToken` DB table keyed by `jti`) is
deliberately not part of MVP. Add it later if `POST /auth/logout` (one
session) becomes a requirement.

### Rate limiting

`InMemoryLoginRateLimiter` blocks per-email after a threshold of failed
login attempts. **Not distributed**: under `uvicorn --workers N` the
limit becomes `N × threshold`. Swap in a backend that satisfies
`RateLimiterBackend` (Protocol introduced in PR-9) for production
multi-worker deployments.

## Authorization

Permission keys: `admin.<resource>.<action>`. Wildcards only at the
trailing segment.

| Granted on a role | Required for an endpoint | Match? |
|---|---|---|
| `admin.*` | `admin.posts.list` | yes |
| `admin.posts.*` | `admin.posts.delete` | yes |
| `admin.posts.delete` | `admin.posts.delete` | yes |
| `admin.posts.list` | `admin.posts.delete` | no (403) |
| `admin.*.list` | anything | **rejected on parse** — middle wildcards are illegal |

The CRUD router computes the required key per endpoint and calls
`AdminContext.has_permission(required)` which delegates to the
wildcard-aware helper.

### Single-tenant / no-tenant scope

Permission keys are a *tenant-role* concept. With no tenant context
(single-tenant deployments, or root scope) there is no role system to gate
by, so the admin surface (CRUD, actions, import/export) requires a
**superadmin** by default — otherwise any authenticated, active account
could manage everything. Controlled by
`CoreAdminConfig.single_tenant_require_superadmin` (default `True`); set it
`False` only if you deliberately want every authenticated caller to have
full access.

Note on revocation: clearing `is_superadmin` is re-evaluated on the next
request, but it does **not** invalidate an already-issued JWT (the token
stays valid until it expires). To cut existing sessions immediately, bump the
user's `token_version` (logout-all) or set `is_active=False` (rejected on the
next request).

## Input validation

Every external identifier passes through
`asterion.security.validation`:

```python
validate_resource_name(value)     # ^[a-z][a-z0-9_-]{0,62}$
validate_action_name(value)       # ^[a-z][a-z0-9_]{0,62}$    (no hyphens)
validate_tenant_slug(value)       # ^[a-z][a-z0-9-]{1,62}$
validate_schema_name(value)       # ^[a-z][a-z0-9_]{0,62}$ + reserved-name guard
validate_permission_key(value)    # ns.resource.action with trailing-* only
validate_limit_offset(limit=, offset=)  # bounded [1, 500]; offsets clamped to 0
```

Resource/action/key validators are invoked from the registry, the CRUD
router, the actions router, the contract router, the permissions sync
flow, and `permission_key()`. Tenant slugs + schema names are validated
at tenant create + bootstrap time. Pagination is normalized in
`crud.query.normalize_limit_offset`.

## Field protection

`ModelAdmin` exposes two protection knobs:

```python
class WidgetAdmin(ModelAdmin):
    readonly_fields  = ["id", "created_at", "updated_at"]   # 422 on mutation
    protected_fields = ["internal_token"]                   # never serialized, never accepted
```

Plus the framework-wide `ProtectedFieldRegistry` (seeded from
`DEFAULT_PROTECTED_FIELDS`; extensions add more via
`register_protected_fields`) which always masks `hashed_password`,
`password`, `secret`, OAuth tokens, etc. no matter what the per-admin
config says. These rules apply to:

- contract `/api/v1/admin/_contract/{resource}` (hidden + globally protected dropped)
- serializer output (list + detail responses omit them)
- payload validator `clean_write_payload` (writes rejected with 422)

### One resolution rule (Roadmap A0.4)

There are three *inputs* to a field's effective permission — `protected_fields`
(→ `HIDDEN`), `readonly_fields` (→ `READ`), and the per-user, per-row
`AdminPolicy.field_permission()` — but **one** resolver combines them:
`FieldPermission.strictest(...)` (`WRITE < READ < HIDDEN`). The static
class (`static_field_permission`) is computed from the admin's lists, then
the policy result is folded in with `strictest`.

The invariant: **a policy can only *tighten*, never loosen.** A field that
is statically `READ`/`HIDDEN` can never be widened to `WRITE` by a policy.
The three knobs are kept as ergonomic shortcuts on purpose — they are not
competing mechanisms, just inputs to the single `strictest` rule
(see [`asterion/admin/policy.py`](../asterion/admin/policy.py),
tested in `tests/crud/test_field_permission_resolution.py`).

## Secret sanitization

`asterion.security.sanitize.sanitize_payload(payload)` recursively
walks a dict/list and redacts values under keys that contain (with
word-boundary matching) any of:

```text
password, hashed_password, password_hash, new_password, current_password,
token, access_token, refresh_token, secret, secret_key,
authorization, cookie, set_cookie, api_key, apikey, private_key
```

Audit `changes` columns and log records pass through this before they
reach the database / log handler. Word-boundary matching means `tokens`
(plural) is not a sensitive key but `access_token` is.

## Audit

Every login, CRUD write, admin action, and impersonation start writes
one `AuditLog` row. Rows are written in two modes:

- **In-session, savepoint-isolated** (`record_audit_in_session`) for CRUD
  + actions. Commits with the main txn; audit-insert failures are caught
  and don't break the response.
- **Isolated session** (`record_audit`) for login (which `raise`s after
  audit on failure paths — an in-session row would otherwise roll back).

Audit values pass through the sanitizer before insert.

## Error envelope

Every error response uses one shape so clients can rely on
`error.code` and `error.message`. See [`docs/architecture.md`](architecture.md)
for the canonical codes and to [`asterion/core/errors.py`](../asterion/core/errors.py)
for how to raise custom envelopes via `AdminError`.

## Known limitations (be honest)

- PostgreSQL schema isolation is only **proven** if `pytest -m postgres`
  runs in CI against a real PG service. SQLite tests cannot verify it.
  (It does run in CI — see `.github/workflows/ci.yml`.)
- The in-memory rate limiter is not distributed.
- **Root / Audit / CLI are builtin-`User` coupled (Roadmap A0.5).**
  External `user_mode` fully covers auth + CRUD + contract via the
  providers, but `root/*`, `audit/service.py`, `tenancy/bootstrap.py`
  and `cli/main.py` still import the concrete builtin `User` model. An
  external IdP works for the admin surface; superadmin/root tooling and
  audit-actor resolution remain builtin-only by design for now. Full
  decoupling is tracked in [review-hardening-roadmap.md](review-hardening-roadmap.md).
- No CSP by default (the bundled UI's inline config scripts would break a
  strict `script-src 'self'`). Set `CoreAdminConfig.content_security_policy`
  (or `ASTERION_CONTENT_SECURITY_POLICY`) to emit one — recommended for
  API-first deployments with their own frontend, e.g.
  `default-src 'self'; frame-ancestors 'none'`. Note the built-in UI keeps the
  access token in `localStorage`, so a strict CSP is the main defence against
  token theft via XSS; nonce-based hardening of the bundled UI's inline
  scripts is a tracked follow-up.
- Client IP (tenant IP allowlist + audit `ip_address`) defaults to the direct
  peer and **ignores `X-Forwarded-For`**. Behind a reverse proxy set
  `CoreAdminConfig.trusted_proxy_count` (or `ASTERION_TRUSTED_PROXY_COUNT`)
  to the number of trusted proxy hops, and run `uvicorn --proxy-headers` — else
  the per-tenant IP allowlist sees only the proxy IP and the audit IP is wrong.
  Never set this above the real hop count: it would let clients spoof the IP
  via the header.
- The default login rate limiter is in-memory (per process) and keyed by email
  only. For multi-worker production wire a shared backend
  (`asterion.extensions.rate_limit_redis`); `(email, ip)` keying is a
  planned follow-up that builds on `trusted_proxy_count`.
- No automatic audit retention; run `DELETE FROM audit_logs WHERE created_at < NOW() - INTERVAL '90 days';`
  on a schedule.
