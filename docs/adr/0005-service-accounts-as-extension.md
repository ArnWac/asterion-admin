# ADR-0005 — Service accounts as an extension; core keeps a generic password-login-disabled mechanism

**Status:** Accepted

## Context

Token-only "service accounts" (a stationary time-clock terminal, a
service-to-service caller) authenticate with a minted access token and never a
password. Until v0.1.52 the feature lived in core: a boolean
`User.is_service_account` column, provisioning helpers in
`asterion/auth/service_accounts.py`, CLI commands, and a branch in the
password-reset flow that skipped these accounts.

The framework already treats an *alternative authentication method* — OAuth/OIDC
— as an **extension** (`auth_oauth`): it owns its own `external_identities`
table, a `UserProvider` capability, and its routes, and touches the core `User`
class not at all. Service accounts are the same shape (an alternative auth
method), yet were core — an inconsistency.

But service accounts are **not** a clean drop-in extension the way OAuth is,
because of one genuine core coupling: the password-reset flow must refuse them.
And that decision cannot be reduced to "is this account passwordless" — an
**invited human is also passwordless** yet *must* receive a reset token to set
their first password. So the reset-exclusion is a real core-security invariant
that needs a marker core can read, distinct from mere passwordlessness.

## Decision

Split the concept along the mechanism/feature line:

- **Core keeps a generic auth mechanism, not the domain concept.** The
  `User.is_service_account` column becomes `User.password_login_disabled`
  (migration `0011`, a value-preserving rename): "this account may not
  authenticate with a password and never receives a reset token." Core owns
  *that a login can be disabled*; the password-reset gate checks this flag. It
  is explicitly **not** the same as passwordless — an invited human is
  passwordless with `password_login_disabled = False`, so still reset-eligible.

- **The service-account feature moves to an extension**,
  `asterion/extensions/service_accounts/`, mirroring `auth_oauth`:
  - a public-schema `ServiceAccount` marker table (own table, not a core column
    — `register_models`, no bundled migration; host apps autogenerate);
  - the `create_service_account` / `delete_service_account` provisioning
    helpers (compose core primitives; set `password_login_disabled=True` and
    record a `ServiceAccount` row; the teardown guard reads that table);
  - `ServiceAccountsExtension` to wire the model.

- **The CLI command stays in the framework CLI but delegates to the extension.**
  The extension SPI has no CLI hook and the `asterion` CLI is a single
  entrypoint, so `asterion service-account create/delete` remains in
  `cli/main.py` and lazy-imports the extension helpers. This is an *ops-tool*
  import of shipped-but-opt-in code, not an application-runtime dependency — the
  running app knows nothing about service accounts unless it wires the
  extension.

## Consequences

- **Positive:** consistent story — alternative auth methods (OAuth, service
  accounts) are extensions that own their tables; the core `User` carries no
  service-account concept, only a generic, defensible auth primitive; the
  reset-exclusion invariant is named precisely (`password_login_disabled`, not
  the misleading "passwordless").
- **Negative:** a core migration renames a column (breaking:
  `User.is_service_account` → `User.password_login_disabled`); the extension
  ships no migration, so host apps must autogenerate the `service_accounts`
  table (same rule as `external_identities`); the CLI retains an intra-package
  import of the extension.
- **Data note:** the rename is value-preserving, so existing accounts keep
  correct auth behaviour immediately. Pre-existing service accounts have no
  `ServiceAccount` marker row until backfilled, so they stay token-only and
  login-locked but won't appear in the extension's teardown/listing until a row
  is added.
- **Future:** a formal device-pairing flow (device-code style) belongs in this
  extension, not the core auth path — the extraction creates its natural home.

See [auth-architecture.md](../auth-architecture.md),
[extensions.md](../extensions.md), and [ADR-0002](0002-privacy-as-core-module.md)
(the mechanism/feature rubric this applies).
