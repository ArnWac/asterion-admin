# Basic Multi-Tenant Example

Minimal adminfoundry integration with subdomain-based multi-tenancy. Registers a tenant-scoped `Task` model.

```bash
uvicorn examples.basic_multi_tenant.app:app --reload --host 0.0.0.0
```

- Superadmin panel: http://localhost:8000/admin-ui  (`admin@example.com` / `admin123`)
- Tenant alpha: http://alpha.localhost:8000/admin-ui
- Tenant beta: http://beta.localhost:8000/admin-ui
