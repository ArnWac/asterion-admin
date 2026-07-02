# Privacy & data protection

This document is the **PII inventory** and the **data-subject workflow** for the
data asterion itself stores. It is the accountability record (GDPR Art. 5(2)) for
the framework layer; an embedding application (e.g. Simpletimes) extends it with
its own domain tables.

> Scope: this covers the **framework-owned** tables. Application domain tables
> (time entries, employees, …) live in the tenant schema and are the app's
> responsibility — classify their PII columns via the registry (see
> [Classifying app PII](#classifying-application-pii)).

Related: [DATA_RETENTION.md](DATA_RETENTION.md) ·
[AUDIT_LOGGING.md](AUDIT_LOGGING.md) · [DATA_PROCESSING.md](DATA_PROCESSING.md) ·
[security.md](security.md).

## PII inventory (framework tables)

Legend — **Category**: `IDENTITY` / `CONTACT` / `BEHAVIORAL` / `SENSITIVE` (the
[`PIICategory`](../asterion/privacy/classification.py) values) or *credential*
(secret, never personal-data-exported) / *non-personal*.

### Global schema (`public`)

| Table | Column | Category | Purpose | Notes |
|---|---|---|---|---|
| `users` | `email` | CONTACT | Login id, notifications | Unique. Tombstoned on anonymisation. |
| `users` | `full_name` | IDENTITY | Display name | Nullable. Cleared on anonymisation. |
| `users` | `hashed_password` | credential | Auth | bcrypt+SHA-256 pre-hash; never exported. |
| `users` | `totp_secret` | credential | 2FA | Never exported; cleared on anonymisation. |
| `users` | `is_active`, `is_superadmin`, `password_login_disabled`, `totp_enabled`, `token_version` | non-personal | Auth/state | — |
| `users` | `deactivated_at` | non-personal | Retention clock (G2) | Set on disable; starts the anonymisation timer. |
| `tenants` | `name`, `slug`, `schema_name` | non-personal* | Tenant identity | *Org data; may be personal for a sole-proprietor tenant. |
| `tenants` | `allowed_cidrs`, `timezone`, `language`, `date_*` | non-personal | Tenant config | — |
| `tenant_memberships` | `user_id`, `tenant_id` | link | Who belongs where | FK to `users`/`tenants`. |
| `audit_logs` | `actor_user_id` | link | WHO did an action | FK-less; survives anonymisation. |
| `audit_logs` | `actor_label` | CONTACT | Actor email snapshot | Nulled by `anonymize_audit_actor`. |
| `audit_logs` | `ip_address` | CONTACT | Source IP | Nulled by `anonymize_audit_actor`. |
| `audit_logs` | `changes` | mixed | Change diff | PII-redacted + behavioural-suppressed at write (G5/G7). |
| `impersonation_logs` | `superadmin_id`, `target_user_id`, `reason` | link/governance | Support-access trail | `reason` required by default (G9). |
| `password_reset_tokens` | `token_hash` | credential | Single-use reset | Hash only; raw token never stored. |
| `two_factor_backup_codes` | `code_hash` | credential | 2FA recovery | Hash only. |
| `revoked_tokens` | `jti`, `user_id` | link | Per-token revocation | Self-expires (`expires_at`). |
| `admin_saved_filters` | `user_id`, `payload` | link/pref | Saved list filters | UI preference. |
| `data_subject_requests` | `subject_user_id`, `handled_by_user_id` | link/governance | DSAR register (G8) | Who/what/when/result of an Art. 15-20 request; `note` must not hold exported PII. |
| `permission_catalog`, `tenant_roles`, `tenant_role_permissions`, `tenant_membership_roles` | — | non-personal | RBAC config | — |

### Tenant schema (per tenant, `tenant_<slug>`)

| Table | Column | Category | Purpose |
|---|---|---|---|
| `tenant_audit_logs` | `actor_label`, `ip_address`, `changes` | CONTACT / mixed | Per-tenant audit trail (same shape as `audit_logs`, no `tenant_id`). |
| *app domain tables* | *app-defined* | *app-classified* | Application data — see below. |

## Lawful basis & special categories

The framework stores no Art. 9 special-category data of its own. `BEHAVIORAL`
data (employee activity) only exists in **application** tables; the framework
provides the controls (classification + audit suppression) but does not itself
create behavioural records beyond the audit trail — which is minimised by
default ([AUDIT_LOGGING.md](AUDIT_LOGGING.md)).

## Data-subject lifecycle (erasure — GDPR Art. 17)

asterion implements a **two-stage** lifecycle rather than a hard `DELETE` (a row
delete would orphan FK-less references and leave the actor's email behind in the
audit log — an *incomplete* erasure):

```
active ──disable──► inactive (reversible) ──[retention period]──► anonymised (final)
        token dead, deactivated_at set            PII tombstoned, row kept
```

1. **Deactivate (stage 1, reversible).** `asterion user disable --email …` sets
   `is_active=False`, bumps `token_version` (kills live tokens) and stamps
   `deactivated_at`. Equivalent to "restriction of processing" (Art. 18) and
   starts the retention clock. `user enable` reverses it.
2. **Anonymise (stage 2, irreversible).** Tombstones every PII field on the
   `users` row (email → `anonymized-<id>@anonymized.invalid`, name/2FA cleared,
   password replaced with an unknowable hash) **and** nulls the actor PII
   (`actor_label`, `ip_address`) in the public **and** every tenant audit log.
   The row survives so audit / foreign-key integrity holds.
   - **Manual:** `DELETE /api/v1/root/users/{id}` (superadmin only) or
     `asterion user anonymize --email …`.
   - **Automatic:** set `user_anonymize_after_days`; `asterion privacy
     retention-run` then anonymises accounts deactivated longer ago than that.
     See [DATA_RETENTION.md](DATA_RETENTION.md).

> **Retention conflict.** Working-time / payroll records carry statutory minimum
> retention (e.g. §16 ArbZG, tax law up to 10 years). Set
> `user_anonymize_after_days` **above** any such minimum, and keep domain data
> only as long as the law requires — anonymisation of the *account* does not
> delete domain rows the app must keep.

## Data-subject access / portability (Art. 15 / 20)

`export_subject(db, user_id)` (G8) assembles a JSON bundle of **everything
asterion holds about one user** across the public/global tables — the `users`
row (minus secrets like `hashed_password` / `totp_secret`, dropped via the
`ProtectedFieldRegistry`), tenant memberships, the audit actions the user
performed, impersonation rows they were party to, their saved filters, and their
DSAR history. Trigger it via:

- `GET /api/v1/root/users/{id}/export` (superadmin only), or
- `asterion privacy export-subject <user_id> --out subject.json`.

**Scope is public/global only — never a foreign tenant's schema.** Tenant-local
business data is the operator's domain model; dumping it generically would risk
crossing tenant boundaries, so the bundle is explicit about that limit. A
deployment fulfils the *complete* access request by combining this bundle with a
per-tenant export of the app's domain tables (see
[tenancy.md](tenancy.md#offboarding-a-tenant) for the schema-dump primitive).

### DSAR register (accountability, Art. 5(2))

Every request is logged in the `data_subject_requests` table — who/what/when/
result. An export auto-logs a completed `access` entry; other rights are
recorded explicitly:

| Right | How | DSAR entry |
|---|---|---|
| Access / portability (Art. 15/20) | `GET …/export` | auto `access` (completed) |
| Rectification (Art. 16) | normal CRUD on the `users` row | `POST …/dsar` `rectification` |
| Erasure (Art. 17) | `DELETE /users/{id}` (anonymise) | `POST …/dsar` `erasure` |
| Restriction (Art. 18) | `user disable` (the documented marker) | `POST …/dsar` `restriction` |

Record/list a request via `POST` / `GET /api/v1/root/users/{id}/dsar` or
`record_subject_request` / `list_subject_requests`. The DSAR row is the
*accountability* record (that a request arrived and how it was handled); the
technical action is separately in the audit log (`subject_export`,
`user_anonymize`, …).

## Employee data protection (§26 BDSG / Art. 88)

The audit trail can otherwise become a continuous value-level monitoring record
of staff. Default-minimal behaviour (G5):

- Fields an app classifies as `BEHAVIORAL` (e.g. a punch-time edit) have their
  **values** suppressed in audit `changes` (`***BEHAVIORAL***`) unless
  `audit_behavioral_detail=True` is set deliberately. The row still records
  *that* the field changed and *by whom* — not the before/after value.
- Support impersonation requires a documented `reason` by default (G9), so
  cross-user access to employee data always carries a purpose.

This is a technical control, not legal advice — works-council / co-determination
obligations remain the operator's responsibility.

## Classifying application PII

PII handling is driven by the contributable
[`PIIFieldRegistry`](../asterion/privacy/classification.py) (G1), keyed by bare
field name. An app classifies its own columns so the anonymiser, redactor and
behavioural guard find them:

```python
from asterion.privacy import PIICategory, get_pii_registry

reg = get_pii_registry()
reg.register("phone", PIICategory.CONTACT)
reg.register("punch_time", PIICategory.BEHAVIORAL)   # value-suppressed in audit by default
```

Register before `create_admin` finishes setup (the registry is frozen
afterwards). Framework defaults are seeded already
(`email`, `full_name`, `actor_label`, `ip_address`).

## See also

- [Privacy as a core module (ADR-0002)](adr/0002-privacy-as-core-module.md)
- [`asterion/privacy/`](../asterion/privacy/) — classification, anonymiser,
  redaction, retention.
