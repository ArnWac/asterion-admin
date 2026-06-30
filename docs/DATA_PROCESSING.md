# Data processing (AVV / DPA technical description)

The technical building blocks a controller needs to complete a data-processing
agreement (AVV / DPA): what data flows where, which external services may act as
sub-processors, and a technical-and-organisational-measures (TOMs) template
grounded in what asterion actually implements.

> asterion is a **framework**, not a hosted service. The *operator* (you, or the
> SaaS provider embedding it) is the processor toward end customers; the external
> services below become **sub-processors** only when you configure them. See
> [shared-responsibility.md](shared-responsibility.md).

## Data flows

```
            ┌─────────────┐     bearer JWT       ┌──────────────────┐
  browser / │  client /   │ ───────────────────► │  asterion app    │
  API client│  bundled UI │ ◄─────────────────── │ (FastAPI)        │
            └─────────────┘    JSON contract      └────────┬─────────┘
                                                           │ SQLAlchemy
                                            search_path-scoped per tenant
                                                           ▼
                                              ┌──────────────────────┐
                                              │ PostgreSQL            │
                                              │ public + tenant_<slug>│
                                              └──────────────────────┘
   optional, only when configured:
     • object storage (FileField uploads)  → S3-compatible store
     • transactional email (reset/invite)  → SMTP / Resend / SES
     • external login                      → OAuth/OIDC IdP
     • distributed rate limiting           → Redis
```

## Sub-processors (only if configured)

| Service | When | Data it sees | Module / dependency |
|---|---|---|---|
| **PostgreSQL** | Always | All stored data (see [PRIVACY.md](PRIVACY.md)) | core (`asyncpg`) |
| **Object storage (S3-compatible)** | `FileField` uploads with the S3 backend | Uploaded files + keys | `storage_s3` extension (`boto3`) |
| **SMTP server** | Password-reset / invite email | Recipient email + token link | `email` extra (`aiosmtplib`) |
| **Resend** | Email via Resend | Recipient email + token link | `email-resend` (`httpx`) |
| **AWS SES** | Email via SES | Recipient email + token link | `email-ses` (`boto3`) |
| **OAuth/OIDC IdP** | External login | Subject id, email, profile claims | `auth_oauth` extension |
| **Redis** | Multi-worker rate limiting | Rate-limit keys (email/IP) | `rate_limit_redis` extension |

Default install uses **only PostgreSQL**. Local file storage
([`asterion/storage/local.py`](../asterion/storage/local.py)) and the dev-only
logging email notifier introduce **no** third party. Each extra is opt-in;
declare in your DPA only the sub-processors you actually enable.

## Where personal data lives

- **At rest:** PostgreSQL (live DB + backups/PITR), and any configured object
  store for uploaded files. PII columns are **not** encrypted at the field level
  yet (roadmap G22) — rely on database-/disk-level encryption at the
  infrastructure layer.
- **In transit:** TLS is the operator's responsibility (reverse proxy / managed
  PG TLS). asterion emits bearer tokens; serve only over HTTPS.
- **In logs:** structured logs and audit `changes` are secret-stripped +
  PII-redacted ([AUDIT_LOGGING.md](AUDIT_LOGGING.md)). Never log raw request
  bodies without `sanitize_payload`.

## Tenant isolation

Each tenant's operational data lives in its **own PostgreSQL schema**
(`tenant_<slug>`), reached via a transaction-scoped `SET LOCAL search_path`.
There is no `tenant_id` filter to forget — isolation is structural. Proven in CI
against real PostgreSQL (`tests/postgres/`). See [tenancy.md](tenancy.md) and
[ADR-0001](adr/0001-schema-per-tenant.md).

## TOMs template (Art. 32)

Map each measure to the asterion control; fill the operator column for your
deployment.

| Measure (Art. 32) | asterion provides | Operator must provide |
|---|---|---|
| Pseudonymisation / anonymisation | Two-stage user anonymisation (G2); audit PII redaction (G7) | Set `user_anonymize_after_days`; schedule `privacy retention-run` |
| Encryption | — (field encryption is G22) | TLS in transit; disk/DB encryption at rest |
| Confidentiality (access control) | JWT auth, tenant RBAC, schema isolation, superadmin-gated root | Least-privilege DB roles; secret management |
| Integrity | Input validation, protected fields, CRUD policies | Restrict DB write access |
| Availability / resilience | Stateless app; pooled DB | Backups, PITR, monitoring, HA topology |
| Auditability | Audit trail (`audit_logs` + per-tenant) | Retention schedule; off-box log shipping; restrict DB write to prevent tampering (G16 pending) |
| Data minimisation | Behavioural suppression (G5); PII redaction (G7) | Classify app PII; don't enable `audit_behavioral_detail` without cause |
| Restricted support access | Impersonation requires a reason (G9); logged | Review impersonation logs |
| Supply-chain security | CI secret scan (gitleaks, gating), dependency advisory scan (pip-audit), CycloneDX SBOM artefact, PII-free-fixtures tripwire (G12) | Review the pip-audit report; pin/upgrade deps; archive the SBOM for your release |

## See also

- [PRIVACY.md](PRIVACY.md) · [DATA_RETENTION.md](DATA_RETENTION.md) ·
  [AUDIT_LOGGING.md](AUDIT_LOGGING.md) · [shared-responsibility.md](shared-responsibility.md)
- [deployment.md](deployment.md) · [email.md](email.md) ·
  [auth-oauth.md](auth-oauth.md)
