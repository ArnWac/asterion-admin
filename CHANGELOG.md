# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows the project's stability policy in
[`docs/review-hardening-roadmap.md`](docs/review-hardening-roadmap.md#release--versionspolitik--10-gate):
while on `0.x`, a minor release may make breaking changes to the public API
or the JSON contract — such changes are called out here. From `1.0` onward
the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The **public API** is the re-exports in `adminfoundry.__all__` plus the
provider Protocols in `adminfoundry/providers/base.py` (pinned by
`tests/public_api/`). The **contract** is `ModelContractMeta`; a breaking
shape change bumps `CONTRACT_VERSION`.

## [Unreleased]

### Fixed
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

### Changed
- CI: the `build` job now depends on `test-postgres`, so PostgreSQL
  integration tests gate the release artifact.
- Docs: `roadmap.md` and `stabilization.md` consolidated into
  `docs/review-hardening-roadmap.md`; tenancy/architecture docs corrected to
  describe where `search_path` is actually applied.

## [0.1.0]

Initial packaged version (development): contract-driven FastAPI admin
framework with JWT auth, RBAC, PostgreSQL schema-per-tenant isolation, audit
log, impersonation, a CLI, and a built-in UI shell. Not yet tagged or
published to PyPI.
