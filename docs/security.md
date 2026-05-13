# Security

## Protected Fields

`GLOBALLY_PROTECTED` in `adminfoundry/admin/model_admin.py` is a `frozenset` that is unconditionally excluded from all admin schemas (list, detail, create, update, contract, UI):

```python
GLOBALLY_PROTECTED = frozenset({
    "hashed_password",
    "password",
    "pin_hash",
    "shared_secret",
    "tenant_salt",
    "setup_code",
    "qr_bootstrap_token",
})
```

Per-admin extra exclusions are declared in `ModelAdmin.protected_fields`. They are merged with `GLOBALLY_PROTECTED` at schema-build time (see `schema_builder.py`).

## Readonly Enforcement

Fields listed in `ModelAdmin.readonly_fields` are excluded from write schemas. Mutation attempts return HTTP 422. This is enforced at the Pydantic boundary — not only in the UI or registry metadata.

## Authorization

All authorization checks happen server-side:

- `get_current_user` — decodes and validates the JWT. Raises 401 on failure.
- `require_superadmin` — raises 403 for non-superadmin users.
- `_check_model_access()` in `admin/router.py` — per-model access gate (superadmin, impersonation, tenant admin, role-based).
- `_enforce_method_caps()` — DB-backed per-HTTP-method capability check (`RolePermission` rows).
- `policy_engine` in `authz/policy_engine.py` — field-level visibility and editability.

## Tenant Isolation

- `request.state.tenant` is set exclusively by `TenantMiddleware` — never from client-provided query parameters.
- `_tenant_filter()` in `admin/router.py` generates the `WHERE tenant_id = ?` clause. It cannot be bypassed by clients.
- `SchemaTenantStrategy` validates schema names against `^tenant_[a-z0-9_]+$` before interpolating into `SET search_path` SQL. This prevents injection through the raw SQL path.
- Cross-tenant access from a tenant-scoped context is blocked at the CRUD layer.

## Superadmin Tokens

- Superadmin routes do not accept impersonation tokens unless the handler explicitly resolves the impersonation context via `resolve_impersonation_tenant()`.
- Impersonation is restricted to tenant-scoped models (superadmin cannot impersonate to access global models).

## Audit

- `AuditMiddleware` records every admin action (actor, action, model, object_id, changes, tenant_id, IP).
- Audit log records are immutable (`allow_delete=False`, `readonly_fields` covers all columns).
- Audit failures must not silently corrupt or change the functional response path.

## Password / Secret Handling

- Passwords are hashed with bcrypt before storage. Plain-text passwords never appear in the DB.
- `hashed_password` is in `GLOBALLY_PROTECTED` and is never returned by any admin endpoint.
- The admin `UserAdmin.before_create` hook hashes the `set_password` extra field before saving.
