# Architecture Decision Records

Load-bearing, hard-to-reverse decisions are recorded here so the *why* survives
the people who made them. Each ADR is immutable once **Accepted**; revisit by
adding a new ADR that supersedes it (don't rewrite history).

Add an ADR before changing a core invariant — tenant isolation, the privacy
module's location, the token model — or before adopting a decision listed as
"to be decided (ADR)" in [roadmap.md](../roadmap.md) (e.g. RLS as
defence-in-depth G15, field encryption G22).

Format: **Status · Context · Decision · Consequences**.

## Index

| ADR | Title | Status |
|---|---|---|
| [0001](0001-schema-per-tenant.md) | Schema-per-tenant instead of Row-Level Security | Accepted |
| [0002](0002-privacy-as-core-module.md) | Privacy as a core module, not an extension | Accepted |
| [0003](0003-bearer-token-not-cookie.md) | Bearer tokens instead of cookie sessions | Accepted |
| [0004](0004-platform-tier-rbac.md) | Platform authority as a second RBAC tier, not a superadmin boolean | Accepted |
| [0005](0005-service-accounts-as-extension.md) | Service accounts as an extension; core keeps a generic password-login-disabled mechanism | Accepted |
