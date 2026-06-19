# Deployment

This guide covers a real production deployment. For a 30-second
quickstart see [`README.md`](../README.md).

## Required environment

| Variable | Purpose |
|---|---|
| `ASTERION_DATABASE_URL` | `postgresql+asyncpg://user:pass@host:port/db` |
| `ASTERION_SECRET_KEY`   | Random 32+ bytes (`openssl rand -hex 32`). Must NOT equal `change-me-in-production`. |

## Strongly recommended

| Variable | Default | Notes |
|---|---|---|
| `ASTERION_LOG_JSON` | `false` | Set `true` in production for one-line JSON per record. |
| `ASTERION_LOG_LEVEL` | `INFO` | One of `CRITICAL/ERROR/WARNING/INFO/DEBUG/NOTSET`. |
| `ASTERION_CORS_ORIGINS` | `` | Comma-separated allowed origins for browser frontends. |
| `ASTERION_DB_POOL_SIZE` | `10` | Per worker. |
| `ASTERION_DB_MAX_OVERFLOW` | `20` | Per worker. |
| `ASTERION_ENABLE_BUILTIN_UI` | `true` | Set `false` if you serve the UI separately. |

For the full list see `CoreAdminConfig` in
[`asterion/core/config.py`](../asterion/core/config.py).

A starter `.env.example` ships at the repo root.

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

A reference `Dockerfile` + `docker-compose.yml` are at the repo root.

```bash
docker-compose up -d db
docker-compose up --build app
```

## Initial bootstrap

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

`asterion doctor` verifies config + DB connectivity. Run it in your
deployment pipeline.

## Health probes

| Endpoint | Purpose | Suitable for |
|---|---|---|
| `GET /healthz` | Process is alive. Does NOT touch the DB. | `livenessProbe`, load balancer keep-alive |
| `GET /readyz` | Returns 200 only when `SELECT 1` succeeds. | `readinessProbe` |

Sample kubernetes spec:

```yaml
livenessProbe:
  httpGet: { path: /healthz, port: 8000 }
  periodSeconds: 10
readinessProbe:
  httpGet: { path: /readyz, port: 8000 }
  periodSeconds: 5
```

## Logging + correlation

`configure_logging(config)` is called from `create_admin`. With
`ASTERION_LOG_JSON=true` each log line is one JSON object including
the `request_id` when set in the request context. Every response
carries `X-Request-ID`; if your client sends one, the server echoes it,
otherwise it generates a UUID.

Funnel your logs into your aggregator and search by
`request_id="..."` to correlate audit rows, error envelopes, and log
lines for one request.

## Migrations

```bash
# After every model change in the public schema
asterion migrate generate -m "add posts.summary" --env shared
asterion db upgrade-public

# After every model change in tenant-local schemas
asterion migrate generate -m "add tenant_roles.color" --env tenant
asterion db upgrade-tenants
```

`db upgrade-tenants` iterates every active tenant. Run it after every
deploy that introduces a tenant schema migration.

## Multi-worker considerations

- `uvicorn --workers N` runs `N` independent processes. Each gets its
  own `DatabaseManager` pool, its own in-memory rate limiter, and its
  own in-memory tenant cache. The first two are listed as known
  limitations in [`docs/security.md`](security.md).

- CSRF is not an issue for token-in-`Authorization`-header auth. If you
  switch to cookie auth, add CSRF protection.

## Static assets

`/admin/static/*` serves the bundled `admin.css` and `admin.js`. The
shell is intentionally minimal — drive real UI work via the
`/api/v1/admin/_contract` endpoint from your own frontend if you need
something richer than a shell.

## Backup

Two schemas matter:

1. `public` — global user table, tenants, audit, impersonation log.
2. Every `tenant_<slug>` — tenant-local RBAC.

`pg_dump` the whole database. Restoring a single tenant is a
schema-level restore (`pg_dump --schema=tenant_acme`).

## Operating notes

- `audit_logs` grows unbounded. Schedule:
  `DELETE FROM audit_logs WHERE created_at < NOW() - INTERVAL '90 days';`
- `impersonation_logs.revoked_at` is set when a future revocation
  endpoint lands. Today the column is set only manually.
- Avoid `is_system=True` rows for things you ever want to delete via
  the API — the CRUD `DELETE` returns 409 for them.
