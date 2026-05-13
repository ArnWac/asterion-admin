# Basic Single-Tenant Example

Minimal adminfoundry integration without multi-tenancy. Registers one toy `Note` model.

```bash
uvicorn examples.basic_single_tenant.app:app --reload
```

- Admin UI: http://localhost:8000/admin-ui
- Login: `admin@example.com` / `admin123`
