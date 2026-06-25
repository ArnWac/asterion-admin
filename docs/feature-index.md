# Feature index

A dense, scannable map of every asterion feature: what it is, the non-obvious
gotcha, and where it's documented in full. This page is deliberately terse — it
exists so a reader (human or LLM) can locate the right feature and its trap fast,
then jump to the full doc. **The linked docs are the source of truth.**

> Maintenance: when you add or change a feature, update its one-liner here and
> the full section in the linked doc. Don't expand entries into prose — keep this
> a lookup, not a second copy of the docs.

## ModelAdmin options

Full reference: [model-admin.md](model-admin.md). Resource name = model
`__tablename__`. Contract drives the UI; capabilities are per-caller.

| Option | One-liner | Gotcha |
|---|---|---|
| `model` | Required mapped class. | Scope (tenant vs global) is derived from the declarative base, not configurable. |
| `label` / `label_plural` / `description` | Display strings. | Default from `model.__name__`. |
| `list_display` | List columns + order. | Protected fields never emitted even if listed. |
| `ordering` | Default `ORDER BY`; `-` = desc. | `?ordering=` overrides per request. |
| `search_fields` | Enables `?search=` (ILIKE OR). | No `search_fields` → `?search=` ignored. |
| `filter_fields` | Enables `?filter_<col>=`. | Filter on an undeclared column → `422` (opt-in per column). |
| `date_hierarchy` | `?dh=YYYY[-MM[-DD]]` drill-down. | Dropped if column missing / not a date type. |
| `list_editable` | Inline-editable list cells. | Must be in `list_display` + writable; saved via the normal update endpoint. |
| `list_badges` | `{col:{value:style}}` colored cells. | Styles limited to neutral/success/warning/danger/info; values matched as strings. |
| `show_in_nav` | Hide from sidebar, keep routable. | Default `True`. |
| `readonly_fields` | Visible, rejected on write (`422`). | PK + `id/created_at/updated_at/...` already auto-readonly. |
| `protected_fields` | Hidden everywhere + write-rejected. | Adds to the global set; never leaks even if in `list_display`. |
| `calculated_fields` | `{name: callable(obj)}` read-only computed. | Exception in callable → `null`, never `500`. |
| `fieldsets` | `[Fieldset(...)]` form sections. | Unknown/protected fields silently dropped from a section. |
| `form_layout` | `"sections"` (default) or `"tabs"`. | Ignored without fieldsets; bad value → `"sections"`. |
| `widgets` | Per-field widget override. | UI knows `textarea`/`select`/`foreign_key`; others pass through. |
| `placeholders` | Per-field input placeholder. | — |
| `field_conditions` | Show field while `{field, equals|in}`. | Hidden field should be nullable (no value submitted); bad rule → always visible. |
| `field_dependencies` | Narrow `<select>` by a controlling field. | Only meaningful for selects; bad rule → static choices. |
| `display_field` | Label column when this model is an FK target. | Unset → `label_field` heuristic. |
| `actions` | `[AdminAction(...)]` bulk ops. | Permission key = `admin.<resource>.<action.name>`. |
| `inlines` | `[InlineAdmin]` child rows. | Inline writes share the parent transaction (all-or-nothing). `widget="dual_list"` → transfer widget over `value_field`, options from `resolve_options` via `/{resource}/_inline_options/{inline}`. |
| `policy` | `AdminPolicy` object/field gates. | Can only tighten, never loosen, static field perms. |
| `superadmin_only` | Restrict all routes to superadmins. | Closes the in-tenant `admin.*` → global-resource cross-tenant read. |
| `singleton` | One-row-per-tenant settings page. | Counts via the request session (per-tenant); **no** DB constraint; explicit `policy` wins. |

### ModelAdmin methods/hooks

| Hook | When |
|---|---|
| `resolve_list_labels` | Batched `<col>__label` for list + detail. One query per related table. |
| `resolve_fk_options` | Custom FK dropdown (cross-schema / join labels). Return `None` to fall back; scope to `ctx.tenant`. |
| `before_validate` / `validate_create` / `before_create` / `after_create` | Create order (after policy `can_create`). |
| `validate_update` / `before_update` / `after_update` | Update order (after fetch + `can_update_object`). |
| `before_delete` / `after_delete` | Delete order (then `is_system` 409 guard). |

Hooks fire for every mutation path; `ctx=None` (non-HTTP callers) skips them.

### Policies — [security.md](security.md#field-protection)

`AdminPolicy` gates: `can_view_model / can_create / can_view_object /
can_update_object / can_delete_object / field_permission`. `FieldPermission`:
`WRITE < READ < HIDDEN`, combined via `strictest` (policy can only tighten).
`ReadOnlyPolicy` (no writes), `NoCreateDeletePolicy` (update-only).

## Multi-tenancy — [tenancy.md](tenancy.md)

| Feature | One-liner | Gotcha |
|---|---|---|
| Schema-per-tenant | Each tenant = a `tenant_<slug>` schema; isolation via `search_path`. | PostgreSQL only — SQLite shares one namespace, proves nothing. |
| Global vs tenant tables | `public.*` (users/tenants/audit) vs `tenant_<slug>.*` (RBAC). | Tenant-local tables have **no** `tenant_id` column by design. |
| `get_async_session` | Sets `SET LOCAL search_path` per txn. | `SET LOCAL` evaporates on commit — no leak across pooled conns. |
| Tenant resolver | `tenant_resolution="header"` (default) or `"subdomain"`. | Caches slug→context 30s in-process. |
| Provisioning | `tenant create` → `db upgrade-tenant` → `bootstrap`. | Bootstrap is idempotent; seeds owner/admin/viewer roles. |
| Member mgmt | `/_members` CRUD, tenant-scoped. | Foreign `membership_id` → `404` (never `403`); DELETE keeps the global User. |
| Invites | Unknown email → inactive passwordless User + invite token. | Completed via `password-reset/confirm`; default notifier logs token (dev only). |

## Auth & identity — [auth-architecture.md](auth-architecture.md)

| Feature | One-liner | Gotcha |
|---|---|---|
| Four providers | `AuthProvider / UserProvider / TenantProvider / PermissionProvider`. | The only seam; routes only read `AdminContext`. |
| Neutral DTOs | `AuthIdentity`, `AdminPrincipal`, `AdminTenant`. | Framework never sees your `User` ORM row. |
| `AdminContext` | `require_admin_context` (401) vs `build_admin_context` (anon-ok). | `is_superadmin`, `has_permission(key)`. |
| Builtin providers | JWT auth + SQLAlchemy user + tenant middleware + RBAC perms. | RBAC perm lookup needs `search_path` → SQLite returns `frozenset()`. |
| Superadmin | `is_superadmin` → `{"admin.*"}`. | Matches only `admin.*` namespace; other namespaces must check `ctx.is_superadmin`. |
| Service accounts | `create_service_account` token-only machine user. | Passwordless, `is_service_account=True`, excluded from password reset. |
| External auth boundary | Covers auth/CRUD/contract. | root/audit/CLI still import builtin `User`. |

## Security — [security.md](security.md)

| Feature | One-liner | Gotcha |
|---|---|---|
| JWT tokens | `access` + `impersonation` types. | Impersonation tokens rejected at superadmin routes. |
| Revocation | Bump `User.token_version` (logout-all) or `is_active=False`. | Clearing `is_superadmin` doesn't kill an issued JWT. |
| Permission keys | `admin.<resource>.<action>`, trailing-`*` only. | Middle wildcard (`admin.*.list`) rejected on parse. |
| Single-tenant scope | No tenant → superadmin required by default. | `single_tenant_require_superadmin=False` to open it. |
| Input validation | `validate_*` on every external identifier. | Pagination bounded `[1,500]`. |
| Field protection | Per-admin + global `ProtectedFieldRegistry`. | One `strictest` rule; policy only tightens. |
| Secret sanitization | `sanitize_payload` redacts secret-ish keys in audit/logs. | Word-boundary match: `access_token` redacted, `tokens` not. |
| Audit | One row per login/CRUD-write/action/impersonation. | No auto-retention; schedule a `DELETE`. |
| Proxy / client IP | Ignores `X-Forwarded-For` by default. | Set `trusted_proxy_count` + `--proxy-headers`; never above real hop count. |

## Extensions — [extensions.md](extensions.md)

| Feature | One-liner | Gotcha |
|---|---|---|
| `AdminExtension` | Subclass, set `name`, override hooks. | Dependency arrow is strictly extension→core (AST-enforced). |
| Lifecycle | configure → register_* → routes → freeze → startup/shutdown. | Registries freeze after `register_routes`; later add → `RegistryFrozenError`. |
| Registries | permission / protected-field / contract / navigation / admin-page. | Each writable only in its own hook. |
| Perm keys w/o extension | `create_admin(permissions=[...])`. | Merged before extension hooks; auto `admin.<resource>.*` still derived. |
| `register_models` | Ship ORM tables. | Must subclass `GlobalBase`/`TenantBase`; framework ships **no** migration for them. |
| Mounting routes | Often unneeded — `app.include_router(..., prefix="/api/v1/admin")` after `create_admin`. | Path must not equal a registered resource name. |
| Bundled | `import_export`, `auth_oauth`, `email`, `rate_limit_redis`. | — |

## Email — [email.md](email.md)

| Feature | One-liner | Gotcha |
|---|---|---|
| Notifiers | `SmtpEmailNotifier` / `ResendEmailNotifier` / `SesEmailNotifier`. | One instance serves both invite + password-reset keywords. |
| Templates | `<name>.subject.txt/.txt/.html`, Jinja, override by filename. | Without `jinja2` → built-in plaintext. |
| Custom events | `register_template` + `await mailer.send(event, to, ctx)`. | Unknown event → `KeyError`. |
| Outbox | `OutboxEmailNotifier` persists in the same txn; `process_outbox` worker drains. | Ships no migration for `email_outbox`; import the model + autogenerate. |

## OAuth / OIDC — [auth-oauth.md](auth-oauth.md)

| Feature | One-liner | Gotcha |
|---|---|---|
| `OAuthExtension` | Google ships; others via `GoogleOIDCProvider` subclass. | Ships no `external_identities` migration. |
| `auto_create_users` | Default `False` (pre-provisioned only). | `True` requires `email_verified`; refuses email-collision linking. |
| Security | state+nonce+PKCE, RS256-only, JWKS cache, `__Host-` cookie, token in URL fragment. | — |

## CLI & deployment — [deployment.md](deployment.md)

| Command / topic | One-liner | Gotcha |
|---|---|---|
| `db upgrade-public` | Run shared migrations (bundled in wheel). | Works package-relatively; no checkout needed. |
| `db upgrade-tenant(s)` | Run tenant migrations per schema. | Applies asterion's framework tenant base first (own `alembic_version_asterion_tenant`), **then** the app's `alembic_tenant.ini` tree — both, in order, not either/or. |
| `permissions sync` | Write catalog from registry. | Needs `ASTERION_APP=app:app`. |
| `create-superadmin` / `tenant create` / `service-account` | Bootstrap helpers. | — |
| `doctor` | Verify config + DB connectivity. | Run in the deploy pipeline. |
| Health | `/healthz` (no DB) vs `/readyz` (`SELECT 1`). | Liveness vs readiness. |
| Multi-worker | Per-process pool + rate limiter + tenant cache. | Wire a shared rate-limit backend for prod. |

## Architecture — [architecture.md](architecture.md)

Runtime state lives on `request.app.state.asterion` (`AdminRuntime`): `config`,
`db`, `registry`, `providers`, `extensions`, `permission_registry`,
`contract_contributions`, `navigation`, `protected_fields`. No module globals —
multiple `create_admin()` apps can coexist in one process.
