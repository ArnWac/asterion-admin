# Architecture

## Package Layout

```
adminfoundry/
  __init__.py              # Public API: create_admin, ModelAdmin, admin_site, CoreAdminConfig, …
  settings.py              # Pydantic settings (env-based)
  database.py              # SQLAlchemy engine, get_db, get_admin_db
  cli.py                   # Typer CLI (create-superadmin, inspect-registry, doctor, …)
  auth.py                  # JWT, hashing
  auth_provider.py         # AuthProvider — pluggable user-model bridge
  dependencies.py          # FastAPI Depends: get_current_user, require_superadmin, require_role
  signals.py               # Async event bus
  webhooks.py              # Webhook dispatcher
  cache.py                 # Cache abstraction (Redis or memory)
  storage.py               # File storage abstraction
  i18n.py                  # t() translation helper
  dashboard.py             # DashboardWidget registry

  admin/
    model_admin.py         # ModelAdmin base class + GLOBALLY_PROTECTED set
    registry.py            # admin_site singleton (Registry)
    router.py              # create_admin(), CRUD routes, contract routes
    contract.py            # build_model_contract() — renderer-independent metadata
    schema_builder.py      # Dynamic Pydantic schema generation (read/write, field filtering)
    capabilities.py        # Per-user capability computation
    navigation.py          # Navigation structure builder
    actions.py             # AdminAction base class
    serializer.py          # Model → dict serializer (handles computed fields)
    filter_builder.py      # SQLAlchemy filter construction from query params
    ui_renderer.py         # Support matrix (feature flags surfaced to clients)
    ui_preferences.py      # Per-user UI preference persistence

  tenancy/
    context.py             # TenantContext dataclass — runtime tenant snapshot
    resolver.py            # Slug extraction, cache (Redis + memory), resolve_tenant()
    middleware.py          # TenantMiddleware (slim, delegates to resolver)
    strategy.py            # TenantStrategy Protocol
    schema_strategy.py     # SchemaTenantStrategy — per-schema engine cache + validation
    __init__.py            # Public re-exports

  middleware/
    tenant.py              # Shim → adminfoundry.tenancy.*
    audit.py               # Audit log middleware
    errors.py              # Validation exception handler
    logging.py             # JSON request logging
    rate_limit.py          # In-memory rate limiter
    security_headers.py    # Security headers

  models/
    base.py                # TimestampedBase, GUID type
    user.py                # User
    role.py                # Role, user_roles association
    role_permission.py     # RolePermission (per-model CRUD capabilities)
    tenant.py              # Tenant (schema_name property)
    audit_log.py           # AuditLog
    change_request.py      # ChangeRequest (approval workflow)
    …

  routers/
    auth.py                # /api/v1/auth/*
    users.py               # /api/v1/users/*
    roles.py               # /api/v1/roles/*
    tenants.py             # /api/v1/tenants/*
    audit.py               # /api/v1/audit/*
    health.py              # /health
    admin_ui.py            # /admin-ui/* (built-in lightweight UI)

  schemas/                 # Pydantic request/response models
  core/config.py           # CoreAdminConfig
  extensions/              # Optional: jobs, import_export, billing, observability, workflows
  authz/                   # Policy engine, role-based caps

examples/
  default/                 # Full-featured reference app (wires all middleware + extensions)
  basic_single_tenant/     # Minimal single-tenant quickstart
  basic_multi_tenant/      # Minimal multi-tenant quickstart (subdomain resolution)
  blog/                    # Single-tenant blog demo with computed fields
  saas/                    # Multi-tenant SaaS demo with demo data
```

## Public API

```python
from adminfoundry import (
    create_admin,       # Register admin router on a FastAPI app
    ModelAdmin,         # Base class for admin configuration
    admin_site,         # Global registry singleton
    CoreAdminConfig,    # Framework configuration
    AuthProvider,       # Pluggable auth bridge
    DashboardWidget,    # Dashboard widget descriptor
    signals,            # Async event bus
    webhooks,           # Webhook dispatcher
    cache,              # Cache abstraction
    storage,            # Storage abstraction
    t,                  # i18n translation helper
)
```

**Note:** `CoreAdminConfig` class name is a known inconsistency (the factory was renamed `create_admin`; the config class retains the old prefix). This will be cleaned up in a future pass.

## Request Flow

```
HTTP Request
  → CORS middleware
  → RequestLoggingMiddleware
  → RateLimitMiddleware
  → TenantMiddleware  (sets request.state.tenant: TenantContext | None)
  → SecurityHeadersMiddleware
  → UnhandledExceptionMiddleware
  → FastAPI router dispatch
      → Depends(get_current_user)  (JWT decode → User)
      → Depends(get_admin_db)       (tenant-aware session if MULTI_TENANT)
      → route handler
          → _check_model_access()  (RBAC / policy check)
          → _tenant_filter()       (scoped WHERE clause)
          → schema_builder         (field filtering: protected, readonly)
          → ORM operation
          → AuditMiddleware (post-response)
```

## Key Invariants

- `request.state.tenant` is always a `TenantContext` (or `None`).  Never the raw ORM `Tenant`.
- Schema generation excludes `GLOBALLY_PROTECTED` fields unconditionally.
- Readonly fields are enforced at the Pydantic boundary (422 on mutation attempt).
- `get_admin_db` returns a tenant-scoped session when `MULTI_TENANT=True` and a tenant context is present.
