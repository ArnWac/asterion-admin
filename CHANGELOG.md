# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows the project's stability policy in
[`docs/review-hardening-roadmap.md`](docs/review-hardening-roadmap.md#release--versionspolitik--10-gate):
while on `0.x`, a minor release may make breaking changes to the public API
or the JSON contract — such changes are called out here. From `1.0` onward
the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The **public API** is the re-exports in `asterion.__all__` plus the
provider Protocols in `asterion/providers/base.py` (pinned by
`tests/public_api/`). The **contract** is `ModelContractMeta`; a breaking
shape change bumps `CONTRACT_VERSION`.

## [Unreleased]

## [0.1.11] - 2026-06-22

### Added
- **Superadmin tenant switcher in the admin UI.** In header-resolution
  multi-tenant mode, superadmins get a tenant dropdown at the top of the
  sidebar (populated from `GET /root/tenants`). Selecting a tenant stores the
  slug and reloads; every subsequent admin-API request carries the configured
  tenant header, so the scope-filtered sidebar (0.1.10) swaps to that tenant's
  models and CRUD runs against its schema. "Global (public)" clears the
  selection. The switcher only appears for superadmins (non-superadmins get a
  403 from `/root/tenants`, which hides it) and only in header mode — in
  subdomain mode the host already determines the tenant. No backend change: a
  superadmin already resolves to `admin.*` inside any tenant; this wires the
  existing header path into the UI. The tenant header is attached **only** to
  admin-prefix requests, never to `auth/` or `root/`, so a stale selection
  can't break login or the switcher's own tenant lookup.

## [0.1.10] - 2026-06-22

### Added
- **Context-aware sidebar (multi-tenant).** The full-contract endpoint
  (`/_contract`), which feeds the admin sidebar and dashboard, now lists only
  the resources reachable in the current request scope: outside a tenant
  (public schema) only **global** models appear; inside a tenant only
  **tenant-scoped** models appear. Previously every registered model was shown
  regardless of scope, so a superadmin in the public schema saw tenant-only
  resources (`Projects`, `Tickets`, `Tenant Roles`, …) whose tables don't exist
  in `public` — clicking one 500'd with `relation "…" does not exist`. The
  sidebar now mirrors what the CRUD endpoints actually accept. Single-tenant
  apps (`enable_multi_tenant=False`) are unaffected and still see every model.
- `ModelContractMeta.scope` (`"tenant"` | `"global"`), derived from the model's
  SQLAlchemy base (`TenantModel` → `"tenant"`, everything else → `"global"`).
  Additive field with a safe default (`"global"`), so `CONTRACT_VERSION` stays
  `"2"`; clients that ignore unknown fields are unaffected.

## [0.1.9] - 2026-06-22

### Fixed
- **Tenant migrations silently did not persist on PostgreSQL.** The bundled
  tenant `env.py` issued `SET search_path TO "<schema>", public` *before*
  Alembic's `begin_transaction()`. Under SQLAlchemy 2.0 that `execute`
  autobegins a transaction, so Alembic no longer owned the DDL — and the outer
  `connect()` context rolled it back on exit. Result: `bootstrap_tenant` /
  `asterion db upgrade-tenant(s)` logged "Running upgrade …" but created **no
  tenant tables** (`tenant_roles`, …), so tenant provisioning then failed with
  `relation "tenant_roles" does not exist`. The env now commits the
  `search_path` set (session-scoped, so it survives) before running migrations,
  and pins `version_table_schema` to the tenant schema. Added a PostgreSQL
  regression test that runs the real migration and asserts the tables persist
  (the prior tests built tenant tables via `create_all` or mocked
  `command.upgrade`, so this path was uncovered).

### Changed
- `examples/multi_tenant`: owner accounts are seeded under `…@acme.example.com`
  / `…@globex.example.com` instead of the `.test` TLD. Modern `email-validator`
  (behind `EmailStr` on the login endpoint) rejects reserved special-use TLDs
  like `.test`, so the demo owners could not log in. (`example.com` is the
  RFC-2606 documentation domain and validates cleanly.)

## [0.1.8] - 2026-06-22

Hardens the service-account feature from 0.1.7 into a first-class, manageable
account type.

### Added
- **`User.is_service_account`** (shared migration `0005`) — marks token-only
  service / machine accounts as a first-class type so they can be identified in
  queries / UI and reasoned about by the framework.
  `create_service_account` now sets it. (Run `asterion db upgrade-public` to
  apply.)
- **`delete_service_account`** (`asterion.auth.service_accounts`) — the inverse
  of `create_service_account`: removes the user, its tenant membership, and the
  dedicated `service:<label>` role (with grants + membership link), so tearing
  one down leaves no orphan role. Refuses to delete a non-service user.
- **CLI** `asterion service-account delete --tenant <slug> --email <email>`.

### Fixed
- **Service accounts are excluded from the password-reset flow.**
  `POST /auth/password-reset/request` no longer issues a reset token for a
  service account — previously, if one had been created with a real
  (deliverable) email, a reset could have set a password and turned the
  token-only account into a login-capable one. The response is unchanged (202,
  no enumeration).

## [0.1.7] - 2026-06-22

### Added
- **`create_service_account`** (`asterion.auth.service_accounts`) — provisions
  active, passwordless **service / machine accounts** (token-only auth via
  `create_access_token`), bound to a tenant with a dedicated `service:<label>`
  role carrying the given permission keys. Makes device / service-to-service
  accounts (e.g. a stationary time-clock terminal) possible without downstream
  apps stitching together `User` + `TenantMembership` + tenant RBAC themselves.
  - The account is `is_active=True`, `is_superadmin=False`, and **passwordless**
    (an unusable password hash), so `POST /auth/login` rejects it — it can only
    authenticate via a minted access token. Revocation is the standard
    invariant: bump `token_version` or set `is_active=False`.
  - The helper does **not** mint tokens (separation of concerns); the caller
    does, with `create_access_token(user.id, ...)`.
  - `session` must be tenant-scoped (`SET LOCAL search_path`), like
    `get_async_session` / the CRUD path — the RBAC tables are tenant-local while
    `User` + `TenantMembership` are global.
- **CLI** `asterion service-account create --tenant <slug> --label <x>
  --permission <key> …` — provisions the account and prints one freshly minted
  access token (shown once).

### Changed
- Internal: the "passwordless user + membership" creation in the member-invite
  POST path (`asterion/admin/member_router.py`) was factored into shared
  primitives (`asterion.auth.provisioning`) now reused by both member
  onboarding and `create_service_account`. No behavioural change to member
  onboarding.

## [0.1.6] - 2026-06-21

### Changed
- **The admin CRUD/action routes are no longer a greedy catch-all.** They were
  mounted as a dynamic `/{resource}` (+ `/{resource}/{id}`,
  `/{resource}/_actions/{action}`) that captured *every* single-segment path
  under the admin prefix and 404'd unknown ones. They are now registered
  **explicitly per registered resource** (`/api/v1/admin/employees`,
  `/api/v1/admin/projects`, …) — the registry is frozen before routes mount, so
  all resource names are known. **Apps can now mount their own routes under the
  admin prefix with a plain `app.include_router(...)` after `create_admin()`**
  (e.g. `/api/v1/admin/work-sessions`) without writing an `AdminExtension` /
  `register_routes` and without route-ordering tricks. `register_routes` keeps
  working unchanged — no breaking change for existing extensions.
  - For every **registered** resource, all endpoints are byte-for-byte
    unchanged: same URLs, permission gates, error envelopes, and contract
    (`CONTRACT_VERSION` unchanged).
  - A path under the admin prefix that is **neither** a registered resource
    **nor** an app route now returns the framework's standard 404 envelope with
    a generic message (previously a resource-specific "not registered"
    message). This is deliberate — those paths are now free for apps.
  - A genuine name clash (an app route whose path equals a registered resource
    name) is an explicit, accepted conflict: register your resource under a
    different name or mount your route elsewhere. The framework does not try to
    resolve it magically.
  - Internal: `crud.router` / `actions.router` now expose `build_crud_router`
    / `build_actions_router` (the per-resource handler logic is unchanged and
    simply bound per route).

## [0.1.5] - 2026-06-21

Grows the email extension from "SMTP only" into a small but complete delivery
layer: provider transports, overridable HTML templates, and a transactional
outbox for robust async delivery. All three stay inside
`asterion.extensions.email`; the only core change is one neutral line.

### Added
- **Transactional-email provider adapters** (alternatives to SMTP) — same
  rendering, different transport:
  - `ResendEmailNotifier` (Resend JSON API, extra `[email-resend]` → httpx),
  - `SesEmailNotifier` (Amazon SES via boto3, extra `[email-ses]`).
  Both expose `from_env()` and an injectable `send=` for tests / custom
  pipelines; the SDK is imported lazily.
- **Overridable Jinja templates** (#3) — reset/invite bodies now render from
  `<name>.subject.txt` / `<name>.txt` / `<name>.html`, resolved from an app
  `template_dir` (`ASTERION_EMAIL_TEMPLATE_DIR`) first, then asterion's packaged
  defaults. The `[email]` extra now also pulls `jinja2`; without it the
  notifiers fall back to the built-in plaintext. Subclassing `render_*` still
  works for full control. Default reset/invite emails now include an HTML
  alternative.
- **Transactional outbox** (#2, self-contained in the extension) —
  `OutboxEmailNotifier` wraps any notifier and enqueues into an `email_outbox`
  table within the triggering request's transaction (atomic with the
  invite/user); `process_outbox(session, notifier, ...)` drains the queue from
  your own worker with bounded retries + backoff. Following the `auth_oauth`
  convention, the framework ships **no** migration for `email_outbox` — the app
  autogenerates it. `enqueue_email` is exposed for direct use.
- Internal: `BaseEmailNotifier` now holds all transport-agnostic
  rendering/SPI; `SmtpEmailNotifier` and the provider adapters subclass it.

### Changed
- **Core (neutral):** `get_async_session` now stores the request-scoped session
  on `request.state.db_session`, so notifiers/extensions can join the request
  transaction (used by the outbox). Nothing in core depends on reading it.

## [0.1.4] - 2026-06-21

Adds an optional bundled **SMTP email extension** so the password-reset and
member-invite tokens (v0.1.3) can actually be delivered without the host app
writing its own notifier — plus a generic hook for app-defined email events.

### Added
- **`asterion.extensions.email.SmtpEmailNotifier`** (optional extra
  `asterion-admin[email]`, pulls in `aiosmtplib`). One instance satisfies both
  `PasswordResetNotifier` and `InviteNotifier`, so it wires into both
  `create_admin(password_reset_notifier=..., invite_notifier=...)` keywords.
  Build it explicitly or via `SmtpEmailNotifier.from_env()` (reads
  `ASTERION_SMTP_*` + `ASTERION_RESET_URL` / `ASTERION_INVITE_URL`).
  - **App-customisable templates:** override `render_reset` / `render_invite`
    in a subclass to brand the emails (each returns an `EmailContent` with
    subject + plaintext + optional HTML).
  - **Custom email events:** `register_template(event, renderer)` +
    `await mailer.send(event, to, context=...)` let the app send arbitrary
    emails (welcome, receipt, …) through the same transport. Renderers can be
    passed via `SmtpEmailNotifier(templates={...})`, or a subclass can override
    `render_event`.
  - **Pluggable transport:** pass `transport=` (a callable receiving the built
    `EmailMessage`) to route through an app's own mail pipeline or to test
    without a real SMTP server. The `aiosmtplib` dependency is imported lazily,
    so importing the module without the extra is safe.

## [0.1.3] - 2026-06-21

Adds tenant **member management** so a tenant operator can onboard admin users
themselves, instead of every new member having to go through a superadmin or
the CLI.

### Added
- **Tenant member-management endpoints** (`asterion.admin.member_router`),
  mounted under the admin API prefix and strictly scoped to the caller's
  tenant:
  - `GET /api/v1/admin/_members` — list the tenant's members + their roles.
  - `POST /api/v1/admin/_members` — add a member by email. An existing global
    user is linked (idempotent); an unknown email creates an **inactive,
    passwordless** user and issues a single-use **invite** token. Optional
    `role_ids` assign tenant roles on add.
  - `PATCH /api/v1/admin/_members/{id}` — activate/deactivate the membership
    and/or replace its tenant-role set.
  - `DELETE /api/v1/admin/_members/{id}` — remove the membership (the global
    user is left intact — it may belong to other tenants).
  Gated by new built-in permission keys `admin.tenant_members.{list,read,
  create,update,delete}`, seeded onto the default `owner`/`admin` roles by
  bootstrap. Cross-tenant ids resolve to 404, never leaking other tenants'
  rows.
- **Invite delivery SPI** (`asterion.auth.invite.InviteNotifier`,
  `LoggingInviteNotifier` default), wired via
  `create_admin(invite_notifier=...)` — same "framework owns the token, app
  owns delivery" split as the password-reset notifier. Invites reuse the
  single-use `password_reset_tokens` machinery.
- `CoreAdminConfig.invite_token_expire_minutes`
  (`ASTERION_INVITE_TOKEN_EXPIRE_MINUTES`, default 7 days).

### Changed
- `POST /auth/password-reset/confirm` now **activates** the account on a
  successful password set. For a normal reset this is a no-op (the request
  endpoint only issues tokens to already-active users); for an invited user it
  is what completes onboarding. This is the invite-acceptance path — an invited
  user redeems their token at the existing confirm endpoint.

## [0.1.2] - 2026-06-20

Completes the dependency-install story for **tenant provisioning**. v0.1.1
made `db upgrade-public` work from a pip-installed wheel; this release does
the same for `asterion tenant create` / `bootstrap_tenant`, which previously
broke outside a repo checkout.

### Fixed
- **Tenant bootstrap on a pip-installed asterion:** `bootstrap_tenant` ran the
  tenant migrations via a subprocess pointed at a hard-coded repo-root
  `alembic_tenant.ini` (`Path(__file__).parent.parent.parent`), which does not
  exist for a non-editable install — tenant provisioning raised
  `FileNotFoundError`. It now resolves the tenant tree with the same
  package-relative / cwd-aware logic as the `db upgrade-tenant(s)` CLI
  (project-local `alembic_tenant.ini` wins, else asterion's bundled tenant
  migrations) and applies it **in-process** via `alembic.command.upgrade` run
  off the event loop with `asyncio.to_thread` (no subprocess, no nested event
  loop).

### Changed
- The Alembic resolution helpers (`bundled_migrations_path`,
  `shared_alembic_config`, `tenant_alembic_config`, `set_x_schema`) moved from
  `asterion.cli.main` (where they were private `_`-prefixed) to a shared
  `asterion.db.alembic_support` module, now used by both the CLI and tenant
  bootstrap. No public-API change (the CLI helpers were always private).

## [0.1.1] - 2026-06-20

First tagged release — the dependency-installable cut. Notably, asterion's
Alembic migrations now ship inside the wheel, so a pip-installed asterion can
run `asterion db upgrade-public` (the prerequisite for embedding it as a
non-editable dependency in a downstream app). Pin it via
`asterion-admin[postgres] @ git+https://github.com/ArnWac/asterion-admin@v0.1.1`.

### Fixed
- **Packaging:** `python-multipart` is now a **core** runtime dependency (was
  dev-only). The always-mounted storage upload route requires it at import, so
  a clean `pip install asterion-admin` previously failed to even `import
  asterion`. This blocked the non-editable/dependency install path.
- **Single-tenant authorization:** with no tenant context (single-tenant
  deployments / root scope) the admin CRUD/actions/import-export endpoints
  previously allowed **any** authenticated, active account — `is_superadmin`
  did not gate access. They now require a superadmin by default; the new
  `single_tenant_require_superadmin` config (default `True`,
  `ASTERION_SINGLE_TENANT_REQUIRE_SUPERADMIN`) opts back into the legacy
  open behaviour. (Note: revoking `is_superadmin` does not retroactively
  invalidate an already-issued token — bump the user's `token_version` or set
  `is_active=False` to cut existing sessions.)
- **Tenant isolation (PostgreSQL):** the request-scoped CRUD session
  (`get_async_session`) now issues `SET LOCAL search_path` to the resolved
  tenant's schema for the transaction. Previously only the
  `BuiltinPermissionProvider`'s own session was scoped, so tenant-local CRUD
  ran against `public` instead of the tenant schema. The behaviour was masked
  on SQLite by `schema_translate_map`.

### Added
- **Bundled migrations + cwd-independent CLI:** asterion's Alembic migrations
  now live inside the package (`asterion/_migrations/{shared,tenant}`) and ship
  in the wheel via `package-data`, so a pip-installed asterion (no repo
  checkout) can run them. `asterion db upgrade-public` resolves and applies the
  bundled **shared** migrations package-relatively from any cwd.
  `db upgrade-tenant`/`upgrade-tenants` keep the tenant tree app-owned and
  resolve it as: explicit `--config/-c` (or `ASTERION_ALEMBIC_TENANT_INI`) →
  project-local `alembic_tenant.ini` → asterion's bundled tenant migrations.
- **Extension-free custom permissions:** `create_admin(permissions=...)` accepts
  an iterable of keys or a `Callable[[PermissionRegistry], None]`, registered
  before the registries freeze. The keys merge with extension-registered ones in
  `generate_permission_keys()` and land in the catalog on `permissions sync` —
  no `AdminExtension` required. Duplicates (incl. with extensions) are idempotent.
- `tests/postgres/test_http_tenant_isolation.py` — end-to-end tenant
  isolation test over the HTTP CRUD path (create under tenant A is invisible
  to tenant B).
- `CHANGELOG.md` and `SECURITY.md`.
- Test coverage measurement (`pytest-cov`) in CI and a CI status badge in the
  README.
- **Distributed login rate limiter:** `RedisLoginRateLimiter` (duck-typed
  against any async Redis client) behind the existing `RateLimiterBackend`
  Protocol, shipped as the `asterion.extensions.rate_limit_redis` extension
  (mirrors `storage_s3`), plus a `rate-limit-redis` extra.
  `create_admin(login_rate_limiter=…)` swaps it in; the in-memory default is
  unchanged.
- **JWT `iss`/`aud` hardening:** optional `jwt_issuer` / `jwt_audience` config.
  When set, every minted token carries the claim and every decode requires +
  verifies it; unset keeps the historic claim-free behaviour.
- `invalidate_tenant(slug)` and a configurable `tenant_cache_ttl_seconds`
  (default 30) for the per-process tenant resolution cache.
- Tag-triggered release workflow (`.github/workflows/release.yml`): build,
  clean-venv wheel smoke test, and PyPI publish via Trusted Publishing.
- jsdom-based JS tests for `api.js` (`tokenStore`, `APIError` envelope
  parsing); `jsdom` added as a JS dev dependency.
- Optional `content_security_policy` config (R14) — emits a CSP header when
  set; default off so the bundled UI keeps working.
- `trusted_proxy_count` config (R16) + `core.net.client_ip`: the tenant IP
  allowlist and audit `ip_address` now derive the real client IP from
  `X-Forwarded-For` when behind N trusted proxies (default 0 = ignore the
  header).
- Opt-in `login_rate_limit_by_ip` config (R15): keys the login limiter on
  `(email, ip)` instead of email only, so one source can't lock a victim out
  everywhere. Default off.

### Fixed
- Login no longer leaks account existence by timing (R15): the unknown-email
  branch runs a dummy bcrypt verify (`dummy_verify_password`) so it costs the
  same as a wrong-password attempt; unknown email and wrong password return an
  identical 401.

### Changed
- CI: the `build` job now depends on `test-postgres`, so PostgreSQL
  integration tests gate the release artifact.
- Tenant slugs are normalized (strip + lowercase) on both the write path
  (`validate_tenant_slug`) and the read path (`X-Tenant-Slug` / subdomain),
  so client casing/whitespace resolves the canonical tenant.
- Docs: `roadmap.md` and `stabilization.md` consolidated into
  `docs/review-hardening-roadmap.md`; tenancy/architecture docs corrected to
  describe where `search_path` is actually applied.

## [0.1.0]

Initial packaged version (development): contract-driven FastAPI admin
framework with JWT auth, RBAC, PostgreSQL schema-per-tenant isolation, audit
log, impersonation, a CLI, and a built-in UI shell. Not yet tagged or
published to PyPI.
