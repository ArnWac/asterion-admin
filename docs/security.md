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

Two layers, both checked on every request in `get_current_user`:

* **User-wide** via `User.token_version`. Increment the column and every
  previously issued token for that user fails its `tkv` check on the next
  request (this is what "log out everywhere" does; also implicit on deactivate).
* **Per-token** via the `RevokedToken` table keyed by `jti` (`is_token_revoked`
  in [`auth/dependencies.py`](../asterion/auth/dependencies.py)) — single-session
  logout. Tombstones self-expire at the token's own `expires_at`.

### Login rate limiting

`InMemoryLoginRateLimiter` blocks a given email after a threshold of failed
login attempts. It is **not distributed**: under `uvicorn --workers N` the
effective limit becomes `N × threshold`. For multi-worker production, wire a
shared backend that satisfies the `RateLimiterBackend` Protocol — the bundled
`asterion.extensions.rate_limit_redis` provides one.

The same backend type also throttles the **password-reset request** endpoint
(per email, separate counter — `password_reset_rate_limit_max` /
`_window_seconds`) and the **2FA-login** endpoint (per user), so neither can be
abused for enumeration / bombing or second-factor brute force.

**Per-tenant request rate limiting (G19).** Beyond the auth-specific limiters,
`tenant_rate_limit_enabled` turns on a sliding-window budget per tenant
(`tenant_rate_limit_max` / `_window_seconds`): each tenant-scoped request counts
against `tenant:<slug>`, and over budget returns `429` (`rate_limited`). Keyed by
tenant only, so one tenant can never exhaust another's budget — noisy-neighbour
protection. Off by default; requests without a tenant (health, root, login) are
not limited here. The in-process default is per-worker; wire the
`rate_limit_redis` backend (`runtime.tenant_rate_limiter`) for multi-worker.

### Password policy

New passwords (reset + member-invite completion) pass through a pluggable
`PasswordPolicy` (`runtime.password_policy`). The bundled `DefaultPasswordPolicy`
applies **length** (`password_min_length`, ≥ 8; no maximum — the SHA-256 pre-hash
removes bcrypt's 72-byte cap, and NIST SP 800-63B favours length over composition
rules) plus an **opt-in Have I Been Pwned breach check**
(`password_hibp_check`, default off). The breach check uses HIBP's **k-anonymity**
range API — only the first 5 chars of the password's SHA-1 ever leave the process,
never the password or its full hash — and **fails open** (a HIBP outage skips the
check rather than blocking resets). Replace `runtime.password_policy` with any
object satisfying the `PasswordPolicy` Protocol to enforce your own rules.

## Authorization

Authorization is by **permission key**: `<namespace>.<resource>.<action>`.
Wildcards are allowed **only** at the trailing segment. Every access *decision*
goes through one channel — `AdminContext.has_permission(required)` — there is no
parallel `is_superadmin` branch in routers or policies
([ADR-0004](adr/0004-platform-tier-rbac.md)).

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

### Two tiers: tenant vs platform

Keys live in two namespaces, distinguished by **who may assign them**
([ADR-0004](adr/0004-platform-tier-rbac.md)):

- **`admin.*`** (tenant tier) — assignable to tenant roles by a tenant owner.
- **`platform.*`** (platform tier) — for `superadmin_only` global resources;
  assignable only to `PlatformRole`s by a superadmin, and **excluded from tenant
  seeding**, so a tenant owner can never mint platform authority. A superadmin's
  effective grant is `admin.*` + `platform.*`; the flag `User.is_superadmin`
  maps to `platform.*` in the `PermissionProvider` and is **CLI-only** (not
  UI-settable). Graded platform staff hold a scoped subset via a `PlatformRole`.

Because the distinction is a *key*, not a boolean, the "who is a platform
operator" decision is customizable by swapping the `PermissionProvider` — the
correct extension point — not by editing framework gates.

### Single-tenant / no-tenant scope

With no tenant context (single-tenant deployments, or shared/root scope) tenant
roles don't apply. The caller is authorized by their **platform-tier** keys: a
superadmin (`platform.*`) or platform staff holding a scoped `PlatformRole`
grant (resolved by the `PermissionProvider` at shared scope). A caller with no
platform keys falls back to `CoreAdminConfig.single_tenant_require_superadmin`
(default `True`) — otherwise any authenticated, active account could manage
everything; set it `False` only if you deliberately want every authenticated
caller to have full access.

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

Audit `changes` pass through three passes before insert: secret stripping
(sanitizer), PII masking (G7, `audit_pii_mode`), and behavioural-value
suppression (G5, `audit_behavioral_detail`). Retention is handled by
`asterion audit prune --all-tenants` / `asterion privacy retention-run`
(`audit_retention_days`, default 90). Full detail in
[AUDIT_LOGGING.md](AUDIT_LOGGING.md).

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
* **No Content-Security-Policy by default**, but one is fully supported. Set
  `CoreAdminConfig.content_security_policy` (or
  `ASTERION_CONTENT_SECURITY_POLICY`) to emit one. The built-in UI keeps the
  access token in `localStorage`, so a strict CSP is the main defence against
  token theft via XSS — strongly recommended. **With the bundled UI (G10):**
  include the literal `{nonce}` token in your `script-src`; the framework mints a
  per-request nonce, substitutes it into the header, and stamps the UI's inline
  `<script>` blocks with it, so a strict policy covers the UI's own scripts while
  blocking injected ones. Recommended:
  `default-src 'self'; script-src 'self' 'nonce-{nonce}'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'`.
  API-first deployments (no bundled UI) can use any static strict policy
  (`default-src 'self'; frame-ancestors 'none'`) — no `{nonce}` needed.
* **Client IP ignores `X-Forwarded-For` by default** (tenant IP allowlist +
  audit `ip_address` see the direct peer). Behind a reverse proxy, set
  `CoreAdminConfig.trusted_proxy_count` (or `ASTERION_TRUSTED_PROXY_COUNT`) to
  the number of trusted hops and run `uvicorn --proxy-headers`. Never set it
  above the real hop count — that would let clients spoof the IP via the header.
* **Audit rows are not tamper-evident.** They are mutable and prunable — no
  hash-chain / WORM / legal-hold yet (roadmap G16). Restrict DB write access and
  ship logs off-box for a regulated context. See
  [AUDIT_LOGGING.md](AUDIT_LOGGING.md#tamper-evidence-limitation).
* **PII is not encrypted at the field level** (roadmap G22), so live-DB erasure
  (anonymisation) does not propagate into pre-existing backups/PITR. Document a
  backup-rotation window — see
  [DATA_RETENTION.md](DATA_RETENTION.md#erasure-vs-backups-the-hard-part).

## See also

* [Auth architecture](auth-architecture.md) — providers, DTOs, custom identity.
* [Multi-tenancy](tenancy.md) — schema isolation and tenant RBAC.
* [Service accounts](auth-architecture.md#service--machine-accounts) — token-only
  machine callers.
* [Privacy](PRIVACY.md) · [Data retention](DATA_RETENTION.md) ·
  [Audit logging](AUDIT_LOGGING.md) · [Data processing](DATA_PROCESSING.md) —
  data protection.
* [Governance](GOVERNANCE.md) · [Threat model](THREAT_MODEL.md) ·
  [Permission matrix](permission-matrix.md) ·
  [Shared responsibility](shared-responsibility.md) · [ADRs](adr/README.md).
