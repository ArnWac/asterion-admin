# basic_multi — multi-tenant SaaS example

A multi-tenant adminfoundry app with subdomain-based tenant resolution.

## Run

```bash
uvicorn examples.basic_multi.app:app --reload --host 0.0.0.0
```

Demo credentials are printed on startup.

### Tenant URLs

```
http://127.0.0.1:8000/admin-ui           # superadmin / root panel
http://acme.localhost:8000/admin-ui      # tenant: acme
http://orbit.localhost:8000/admin-ui     # tenant: orbit
```

`*.localhost` resolves to `127.0.0.1` automatically on modern operating systems.

### Tenant resolution

The example uses **subdomain-based** resolution, configured explicitly in `app.py`:

```python
config.enable_multi_tenant = True
config.tenant_resolution = "subdomain"
```

To switch to header-based resolution, change `tenant_resolution` to `"header"` and use
`curl -H "X-Tenant-Slug: acme" http://127.0.0.1:8000/health` to select a tenant.

## What's registered

- `UserAdmin`, `RoleAdmin`, `TenantAdmin`, `AuditLogAdmin` — framework models.
- `ProjectAdmin` — tenant-scoped domain model.

Actions used: `DeactivateUsersAction`, `ActivateUsersAction`, `BulkDeleteAction`,
`DisableTenantAction`, `EnableTenantAction` — all imported from `adminfoundry`.
