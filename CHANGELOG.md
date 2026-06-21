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
