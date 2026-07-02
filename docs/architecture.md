# Architecture

This document describes how asterion is put together: the package layout, what
`create_admin()` wires up at boot, the per-request lifecycle, and where runtime
state lives. It is the map to read before diving into any subsystem doc.

## Package layout

```text
asterion/
  __init__.py                # public API: create_admin, ModelAdmin, CoreAdminConfig, AdminRegistry
  py.typed                   # PEP 561 marker

  core/
    app_factory.py           # create_admin() — config → AdminRuntime → FastAPI app
    config.py                # CoreAdminConfig dataclass + from_env()
    runtime.py               # AdminRuntime(config, db, registry, …)
    installers.py            # install_middleware, install_routes
    middleware.py            # RequestIDMiddleware, SecurityHeadersMiddleware
    errors.py                # consistent error envelope + handlers
    logging.py               # configure_logging (plain or JSON)
    health.py                # /healthz, /readyz

  db/
    session.py               # DatabaseManager (one engine, one sessionmaker)
    dependencies.py          # get_async_session — request-scoped session in a txn

  models/
    base.py                  # GlobalBase + TenantBase + GUID + Id/Timestamp mixins
    user.py, tenant.py, tenant_membership.py
    tenant_rbac.py           # tenant-LOCAL: TenantRole, TenantRolePermission, TenantMembershipRole
    permission_catalog.py, audit_log.py, impersonation_log.py

  auth/
    router.py                # POST /login, GET /me, POST /logout-all
    dependencies.py          # get_current_user, require_superadmin (JWT primitives)
    tokens.py                # JWT encode/decode, access + impersonation token types
    password.py              # bcrypt
    rate_limiter.py          # InMemoryLoginRateLimiter
    service_accounts.py      # create/delete token-only machine accounts

  authz/
    permissions.py           # permission_key() + wildcard matcher + require_resource_access
    catalog.py               # generate_permission_keys(registry) + sync_permission_catalog
    registry.py              # PermissionRegistry — in-memory bag extensions write into

  admin/
    context.py               # AdminContext + build_admin_context + require_admin_context
    policy.py                # AdminPolicy, FieldPermission, ReadOnly/NoCreateDelete policies
    fieldset.py, inline.py   # Fieldset, InlineAdmin primitives
    navigation_router.py     # GET /_navigation — per-user, permission-filtered nav

  providers/
    base.py                  # AuthProvider / UserProvider / PermissionProvider / TenantProvider + DTOs
    auth.py, users.py, permissions.py, tenants.py     # Builtin* defaults

  extensions/
    base.py                  # AdminExtension base class (lifecycle hooks)
    registry.py, context.py, lifecycle.py, errors.py
    import_export/, auth_oauth/, email/, rate_limit_redis/

  security/
    validation.py            # validate_{resource_name, action_name, tenant_slug, …}
    sanitize.py              # sanitize_payload(...) — secret redaction for logs/audit
    protected_fields.py      # ProtectedFieldRegistry singleton + DEFAULT_PROTECTED_FIELDS

  registry/
    registry.py              # AdminRegistry
    admin.py                 # ModelAdmin base class

  schemas/
    builder.py               # dynamic Pydantic schemas + field-protection
    fields.py                # AdminModelSchema dataclass
    serialization/serializer.py

  contract/
    service.py               # build_model_contract(ModelAdmin) → ModelContractMeta
    router.py                # GET /_contract, /_contract/{resource}

  crud/
    router.py                # GET/POST/PATCH/DELETE /{resource}[/{record_id}]
    services.py, query.py, payload.py, errors.py, types.py

  actions/
    __init__.py              # AdminAction base + BulkDeleteAction
    router.py                # POST /{resource}/_actions/{action}

  tenancy/
    middleware.py            # TenantMiddleware (resolves slug → TenantContext)
    resolver.py              # slug extraction + cached lookup
    schema_strategy.py       # SET LOCAL search_path
    schema_names.py          # make_tenant_schema_name, validators
    bootstrap.py             # create_tenant_record, seed_default_tenant_roles, bootstrap_tenant
    context.py               # TenantContext (request.state shape)

  audit/
    service.py               # record_audit, record_audit_in_session, audit_payload

  root/
    router.py                # superadmin-only routes aggregator
    impersonation.py         # POST /root/impersonate
    users.py, tenants.py     # global user + tenant read endpoints

  builtins/
    admin.py, installer.py   # tenant RBAC admins + global User/Tenant/ImpersonationLog admins

  cli/
    main.py                  # asterion CLI (db, tenant, permissions, service-account, …)

  ui/
    router.py                # /admin shell routes
    navigation.py            # NavigationRegistry (extension-contributed nav items)
    admin_pages.py           # AdminPage + AdminPageRegistry
    templates/, static/
```

## Application wiring

`create_admin()` turns a `CoreAdminConfig` into a configured FastAPI app:

```text
CoreAdminConfig.from_env()
     │  validate()
     │  configure_logging()
     ▼
ProviderSet(auth=…, users=…, permissions=…, tenants=…)   # Builtin* unless overridden
AdminRuntime(config, db, providers, extensions, permission_registry,
             contract_contributions, navigation, protected_fields)
     │  runtime.extensions.register_all(extensions)
     │  compose_lifespan(extensions, user_lifespan)        # ext.startup/shutdown wraps user lifespan
     ▼
FastAPI(lifespan=composed)
     │  app.state.asterion = runtime
     │  register_error_handlers(app)                       # consistent envelope
     │  install_middleware(app, config)                    # request_id ▸ access_log ▸ security_headers ▸ cors ▸ tenant
     │  install_builtin_admins(registry)                   # tenant RBAC + audit + global (users/tenants/impersonation_logs, platform_only)
     │  register(registry)                                 # your ModelAdmins
     │  run_setup_phase(extensions, ExtensionContext, app) # configure → register_* → freeze registries
     │  install_routes(app, config)                        # /healthz ▸ /auth ▸ /admin/_contract ▸ /admin/_navigation ▸ /root ▸ /admin (UI) ▸ /admin/_actions ▸ /admin/{resource}
     ▼
app
```

Extension routes mount **before** the dynamic `/{resource}` routes, so a
static-path extension route (`/{resource}/_export`) wins over the parameterized
CRUD route. See [Extensions](extensions.md) for the full hook order.

## Request lifecycle

```text
Request
  │
  ▼  RequestIDMiddleware       request.state.request_id
  ▼  AccessLogMiddleware       request/response log records with request_id
  ▼  SecurityHeadersMiddleware X-Content-Type-Options, Referrer-Policy, X-Frame-Options
  ▼  CORSMiddleware            only if cors_origins configured
  ▼  TenantMiddleware          slug → request.state.tenant: TenantContext
  ▼  Route handler
  │    Depends(get_async_session)   opens a txn-scoped session and (PostgreSQL +
  │                                 resolved tenant) SET LOCAL search_path → tenant schema
  │    Depends(require_admin_context)  walks the four providers → AdminContext
  │    Permission check via AdminContext.has_permission()
  │    Body validation via Pydantic + clean_write_payload
  │    Audit via record_audit_in_session (savepoint-isolated)
  ▼  Response                  error envelope on failure; X-Request-ID echoed
```

The provider walk and tenant scoping are detailed in
[Auth architecture](auth-architecture.md) and [Multi-tenancy](tenancy.md).

## Runtime state (no globals)

Everything is reachable through one attribute on the FastAPI app, so the
framework can run multiple `create_admin()` apps in the same process without
state leaking between them:

```python
runtime = request.app.state.asterion       # AdminRuntime
runtime.config                             # CoreAdminConfig
runtime.db                                 # DatabaseManager
runtime.registry                           # AdminRegistry
runtime.providers                          # ProviderSet(auth, users, permissions, tenants)
runtime.extensions                         # ExtensionRegistry
runtime.permission_registry                # PermissionRegistry (extension perm keys)
runtime.contract_contributions             # ContractContributionRegistry
runtime.navigation                         # NavigationRegistry
runtime.protected_fields                   # ProtectedFieldRegistry
```

There is no module-level engine, no settings singleton, and no global
`admin_site`.

## See also

* [ModelAdmin reference](model-admin.md) — the declarative surface most apps
  spend their time in.
* [Auth architecture](auth-architecture.md), [Multi-tenancy](tenancy.md),
  [Security](security.md), [Extensions](extensions.md).
