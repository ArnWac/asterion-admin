# Security Policy

## Supported versions

asterion is pre-1.0 software under active development. Security fixes are
applied to the `main` branch and roll into the next `0.x` release. There is no
long-term support for older `0.x` versions yet.

| Version | Supported |
|---------|-----------|
| `main` / latest `0.x` | ✅ |
| older `0.x` | ❌ |

## Reporting a vulnerability

**Do not open a public GitHub issue for security problems.**

Please report privately via one of:

- GitHub's [private vulnerability reporting](https://github.com/ArnWac/asterion-admin/security/advisories/new)
  (Security → Report a vulnerability), or
- email **arnewacker@gmail.com** with subject `asterion security`.

Please include:

- affected version / commit,
- a description of the issue and its impact,
- reproduction steps or a proof of concept,
- any suggested remediation.

You can expect an acknowledgement within a few days. Once a fix is available
it will be released and the issue credited in `CHANGELOG.md` (unless you ask to
remain anonymous).

## Scope notes

Some limitations are known and documented rather than treated as
vulnerabilities — see [`docs/security.md`](docs/security.md) and
[`docs/review-hardening-roadmap.md`](docs/review-hardening-roadmap.md). Notably:

- The default login rate limiter is in-memory and single-process; multi-worker
  deployments must supply a shared backend.
- Real tenant isolation requires PostgreSQL (schema-per-tenant). SQLite
  collapses all tenants into one namespace and is for development/tests only.

Reports that simply restate a documented limitation may be closed with a
pointer to the relevant doc, but genuine bypasses of the documented behaviour
are in scope.
