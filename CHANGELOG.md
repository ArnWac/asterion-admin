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

### Fixed
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
