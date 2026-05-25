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

## Input validation

Every external identifier passes through
`adminfoundry.security.validation`:

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

## Secret sanitization

`adminfoundry.security.sanitize.sanitize_payload(payload)` recursively
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
for the canonical codes and to [`adminfoundry/core/errors.py`](../adminfoundry/core/errors.py)
for how to raise custom envelopes via `AdminError`.

## Known limitations (be honest)

- PostgreSQL schema isolation is only **proven** if `pytest -m postgres`
  runs in CI against a real PG service. SQLite tests cannot verify it.
- The in-memory rate limiter is not distributed.
- Token revocation is user-wide, not per-token.
- No password reset endpoint in MVP — use the CLI to rotate.
- No strict CSP (the bundled UI's inline scripts would break).
- No automatic audit retention; run `DELETE FROM audit_logs WHERE created_at < NOW() - INTERVAL '90 days';`
  on a schedule.
