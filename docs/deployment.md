# Deployment

This guide covers a real production deployment: required configuration,
containerization, first-run bootstrap, health probes, logging, migrations,
multi-worker considerations, and backups. For a 30-second quickstart see the
[README](../README.md).

## Configuration

### Required environment

| Variable | Purpose |
|---|---|
| `ASTERION_DATABASE_URL` | `postgresql+asyncpg://user:pass@host:port/db` |
| `ASTERION_SECRET_KEY` | Random 32+ bytes (`openssl rand -hex 32`). Must **not** equal `change-me-in-production`. |

### Strongly recommended

| Variable | Default | Notes |
|---|---|---|
| `ASTERION_LOG_JSON` | `false` | Set `true` in production for one-line JSON per record. |
| `ASTERION_LOG_LEVEL` | `INFO` | One of `CRITICAL/ERROR/WARNING/INFO/DEBUG/NOTSET`. |
| `ASTERION_CORS_ORIGINS` | `` | Comma-separated allowed origins for browser frontends. |
| `ASTERION_DB_POOL_SIZE` | `10` | Per worker. |
| `ASTERION_DB_MAX_OVERFLOW` | `20` | Per worker. |
| `ASTERION_ENABLE_BUILTIN_UI` | `true` | Set `false` if you serve the UI separately. |

For the full list, see `CoreAdminConfig` in
[`asterion/core/config.py`](../asterion/core/config.py). A starter
`.env.example` ships at the repo root. Behind a reverse proxy, also set
`ASTERION_TRUSTED_PROXY_COUNT` and run `uvicorn --proxy-headers` — see
[Security § Known limitations](security.md#known-limitations).

## Docker

```dockerfile
FROM python:3.12-slim AS base
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY asterion ./asterion
RUN pip install --no-cache-dir ".[postgres]" uvicorn[standard]
COPY app.py ./
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

A reference `Dockerfile` and `docker-compose.yml` are at the repo root:

```bash
docker-compose up -d db
docker-compose up --build app
```

## First-run bootstrap

```bash
# 1. Public schema
asterion db upgrade-public

# 2. Catalog of permission keys derived from your registry
ASTERION_APP=app:app asterion permissions sync

# 3. First superadmin
asterion create-superadmin --email admin@example.com

# 4. First tenant
asterion tenant create --name "Acme" --slug acme --owner-email owner@example.com
asterion db upgrade-tenant acme
```

`asterion doctor` verifies configuration and DB connectivity — run it in your
deployment pipeline.

## Health probes

| Endpoint | Purpose | Suitable for |
|---|---|---|
| `GET /healthz` | Process is alive. Does **not** touch the DB. | `livenessProbe`, LB keep-alive |
| `GET /readyz` | Returns 200 only when `SELECT 1` succeeds. | `readinessProbe` |

```yaml
livenessProbe:
  httpGet: { path: /healthz, port: 8000 }
  periodSeconds: 10
readinessProbe:
  httpGet: { path: /readyz, port: 8000 }
  periodSeconds: 5
```

## Logging and correlation

`configure_logging(config)` is called from `create_admin`. With
`ASTERION_LOG_JSON=true`, each log line is one JSON object, including the
`request_id` when set in the request context. Every response carries
`X-Request-ID`; if the client sends one, the server echoes it, otherwise it
generates a UUID. Funnel logs into your aggregator and search by
`request_id="..."` to correlate audit rows, error envelopes, and log lines for a
single request.

## Migrations

```bash
# After every model change in the public schema
asterion migrate generate -m "add posts.summary" --env shared
asterion db upgrade-public

# After every model change in tenant-local schemas
asterion migrate generate -m "add tenant_roles.color" --env tenant
asterion db upgrade-tenants
```

`db upgrade-tenants` iterates every active tenant — run it after every deploy
that introduces a tenant-schema migration.

### Where migrations live

asterion's **shared** (public) migrations ship inside the wheel
(`asterion/_migrations/shared`). `db upgrade-public` runs them
package-relatively, so it works from a pip-installed asterion in any working
directory — no repo checkout or `alembic_shared.ini` required.

asterion **owns its tenant tables too** (RBAC + `tenant_audit_logs`), split like
the public/shared tree. `db upgrade-tenant` / `db upgrade-tenants` (and tenant
bootstrap) apply **two** trees per schema, in order:

1. asterion's bundled **framework tenant base** — always applied, tracked in its
   own `alembic_version_asterion_tenant` version table.
2. your app's **tenant tree** for your domain tables, tracked in the default
   `alembic_version`. Resolved from an explicit `--config/-c <path>` (or
   `ASTERION_ALEMBIC_TENANT_INI`), else a project-local `alembic_tenant.ini`.
   When you own no tenant tree the framework base is the whole schema.

This is the key change from earlier versions, which ran exactly one resolved
tree: now the framework tables are guaranteed present even if your app's tree
never imported a framework model — the silent "missing table → 500" footgun is
gone. The framework base migrations are idempotent (they skip tables that
already exist), so an existing app whose own tree already created those tables
upgrades cleanly (the framework version table is simply stamped). Going forward,
**drop the framework tenant tables from your app's tree** and stop importing the
framework tenant models; wire `exclude_framework_tenant_tables` into your tenant
`env.py`'s `include_object` so autogenerate ignores them:

```python
from asterion.db.alembic_support import exclude_framework_tenant_tables
context.configure(..., include_object=exclude_framework_tenant_tables)
```

## Multi-worker considerations

* `uvicorn --workers N` runs `N` independent processes. Each gets its own
  `DatabaseManager` pool, its own in-memory login rate limiter, and its own
  in-memory tenant cache. The first two are listed as known limitations in
  [Security](security.md#known-limitations) — wire a shared rate-limit backend
  for production.
* CSRF is not an issue for token-in-`Authorization`-header auth. If you switch
  to cookie auth, add CSRF protection.

## Static assets

`/admin/static/*` serves the bundled `admin.css` and `admin.js`. The shell is
intentionally minimal — drive richer UI work from the
`/api/v1/admin/_contract` endpoint with your own frontend.

## Backup

Two kinds of schema matter:

1. `public` — global user table, tenants, audit, impersonation log.
2. every `tenant_<slug>` — tenant-local RBAC (plus your domain tables).

`pg_dump` the whole database. Restoring a single tenant is a schema-level restore
(`pg_dump --schema=tenant_acme`).

## Operating notes

* `audit_logs` grows unbounded. Schedule:
  `DELETE FROM audit_logs WHERE created_at < NOW() - INTERVAL '90 days';`
* `impersonation_logs.revoked_at` is set when a future revocation endpoint lands;
  today the column is set only manually.
* Avoid `is_system=True` rows for anything you ever want to delete via the API —
  the CRUD `DELETE` returns `409` for them.

## See also

* [Multi-tenancy](tenancy.md) — provisioning and bootstrap.
* [Security](security.md) — proxy/IP config, rate limiting, audit retention.
* [Email](email.md) — production mail delivery and the outbox worker.
