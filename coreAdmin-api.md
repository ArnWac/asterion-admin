# coreAdmin-api — Project Prompt

## What this project is

coreAdmin is a FastAPI-based, registry-driven admin backend for multi-tenant SaaS applications.

A developer registers their SQLAlchemy models once. coreAdmin generates dynamic CRUD endpoints, a stable admin contract, a lightweight built-in UI, and optionally serves external clients (e.g. a Flutter app) — all without requiring per-model UI code.

### Core capabilities

- Registry-driven CRUD with schema builder, serializer, and filter builder
- JWT authentication with refresh tokens, logout, and token blacklist
- Fine-grained authorization: field-level visibility/editability, record-level restrictions, action gating
- Structured audit logging, impersonation, break-glass editing
- Optional multi-tenancy with PostgreSQL schema isolation
- Approval workflows for high-risk admin changes
- Tenant billing (invoicing tenants for usage)
- Session management, rate limiting, security headers, and observability
- A stable, versioned admin contract for external renderer clients
- A built-in lightweight admin UI as an optional package extra
- A plugin/extension architecture for optional features

---

## Global contract

### Instruction priority

1. Security invariants
2. Feature objective and acceptance criteria
3. Compatibility requirements
4. Reference notes

### Security invariants

- Never expose secrets, hashed secrets, token internals, database credentials, or protected internal fields in any response schema or serialized payload.
- Passwords, hashed passwords, PIN hashes, shared secrets, tenant salts, setup codes, QR bootstrap tokens, and equivalent protected fields must never appear in list, detail, create, or update response payloads.
- Read-only fields must be enforced at the API boundary, not only at UI or registry level.
- Superadmin-only routes must reject impersonation tokens.
- Audit failure must never change the functional response path.
- Billing data must be tenant-scoped and never leak across tenant boundaries.

### Compatibility contract

- Python 3.11+
- Async request and database paths by default
- SQLAlchemy 2.x style
- Pydantic v2
- UUID primary keys server-side
- UTC timestamps
- Absolute imports only
- Public collection endpoints return `PaginatedResponse[T]`

### Conflict handling

If requirements conflict:
1. preserve security invariants
2. preserve external API behavior already established
3. satisfy current feature acceptance criteria
4. choose the smallest internal change
5. leave a brief code comment where a compromise was necessary

### Forbidden shortcuts

- Do not treat SQLite-only success as proof for PostgreSQL-critical behavior.
- Do not use `create_all()` as the only migration verification path.
- Do not expose raw ORM objects via `__dict__`, implicit dumps, or unfiltered generic serialization.
- Do not silently fall back from tenant-scoped access to shared data access.
- Do not weaken protected-field filtering to satisfy generic CRUD generation.
- Do not skip regression tests for existing behavior.
- Do not assume reference snippets are correct if framework constraints or tests prove otherwise.

### Test layers

- **fast**: no Docker required; lightweight isolated DB allowed; used for pure logic, serializers, builders, request validation, auth flow basics, router behavior, and protected-field filtering when PostgreSQL semantics are not essential.
- **integration**: real PostgreSQL required; run migrations; used for constraints, Alembic behavior, tenant schemas, schema scoping, transaction behavior, UUID/JSON/PostgreSQL-specific behavior.
- **e2e**: end-to-end flows through the real app stack; used only for high-value critical paths.

### Migration test rule

PostgreSQL integration tests must apply Alembic migrations. Metadata-only setup is insufficient for tenant and migration verification.

### Output discipline

- Prefer concise code comments over prose.
- Prefer measurable acceptance criteria over qualitative claims.
- Reference snippets are illustrative, not authoritative.

---

## Feature areas

---

### Foundation

**Objective**: settings, database session management, base models, user model, JWT auth, health endpoint, error handling, pagination schema, seed CLI.

**Deliverables**
- `settings.py`
- `database.py`
- `main.py`
- `cli.py`
- `models/base.py`, `models/user.py`
- `schemas/common.py`, `schemas/auth.py`, `schemas/user.py`
- `routers/auth.py`, `routers/health.py`
- `middleware/errors.py`
- `dependencies.py`

**Hard requirements**
- Shared DB session dependency with rollback on exception
- JWT access and refresh tokens
- `/api/v1/auth/login`, `/api/v1/auth/refresh`, `/api/v1/auth/me`
- `/health` reports degraded status if DB check fails
- CORS wired from settings
- Pagination schema defined for all list endpoints
- CLI command `coreadmin create-superadmin`
- Shared Alembic metadata includes shared models

**Acceptance criteria**
- `UserPublic` excludes password and hash fields
- Invalid credentials return 401
- Inactive user cannot authenticate
- Refresh accepts only refresh tokens
- `/me` returns authenticated user only
- Validation errors return normalized JSON shape

---

### User and role management

**Objective**: user CRUD, role model, role checks, superadmin checks, request logging.

**Deliverables**
- `models/role.py`, `schemas/role.py`
- Extended `schemas/user.py`
- Extended `dependencies.py` with `require_role` and `require_superadmin`
- `routers/users.py`, `routers/roles.py`
- `middleware/logging.py`

**Hard requirements**
- Roles are names only — no permission matrix at this layer
- Superadmin bypasses role checks
- User list and role list endpoints paginate
- Duplicate emails and duplicate role assignments are rejected cleanly
- Logging middleware emits request ID and sets `X-Request-ID`
- Logging must not change successful endpoint behavior

**Acceptance criteria**
- Superadmin can create, read, update, and soft-delete users
- Non-superadmin cannot access superadmin-only routes
- Duplicate email creation returns 409
- Role can be created, assigned, removed, and listed
- `require_role("manager")` returns 403 without the role and passes with it
- Soft-delete means `is_active=False`, not physical deletion

---

### Multi-tenancy

**Objective**: optional multi-tenancy behind `MULTI_TENANT` flag; zero regressions when false.

**Deliverables**
- `models/tenant.py`, `schemas/tenant.py`
- `routers/tenants.py`
- `middleware/tenant.py`
- Extended `database.py` with tenant engine cache and tenant DB dependency
- Extended `cli.py` with tenant migration commands (`coreadmin tenant migrate`, `coreadmin tenant upgrade --all`)

**Hard requirements**
- When `MULTI_TENANT=false`, shared mode must behave exactly as before
- When `MULTI_TENANT=true`, tenant-scoped access must require resolved tenant context
- Tenant resolution supports explicit header strategy (`X-Tenant-Slug`) and host-based strategy
- Tenant-scoped DB access must never silently fall back to shared data
- PostgreSQL schema scoping for shared-database tenants
- Tenant routes remain superadmin-only

**Acceptance criteria**
- Tenant create, read, update, disable, and migrate flows work
- Invalid slug returns 422; duplicate slug returns 409
- Disabled tenant is rejected by tenant middleware
- Requests missing tenant context in multi-tenant mode fail with 400
- Tenant A data is not visible under tenant B context
- Single-tenant tests still pass unchanged when flag is false

---

### Registry-driven admin CRUD

**Objective**: model registry, schema builder, filter builder, serializer, and dynamic admin CRUD routes.

**Deliverables**
- `admin/model_admin.py`
- `admin/registry.py`
- `admin/schema_builder.py`
- `admin/filter_builder.py`
- `admin/serializer.py`
- `admin/router.py`
- Package exports: `admin_site`, `ModelAdmin`, `create_coreadmin`

**Hard requirements**
- Schema generation must filter protected fields globally and per-admin config
- Generated create schemas exclude auto and read-only fields
- Generated update schemas make fields optional
- Dynamic CRUD uses filtered schemas, not raw ORM dumps
- Registry metadata must not expose protected fields as editable fields
- Tenant-scoped admin models honor tenant context in multi-tenant mode
- Readonly field mutation attempts return 422

**Acceptance criteria**
- Protected fields are absent from list, detail, create, and update schemas
- Readonly fields produce 422 on attempted mutation
- Dynamic list endpoints paginate and respect filtering, search, and ordering
- Dynamic detail endpoints do not leak protected fields
- Tenant-scoped models return only rows for the active tenant

---

### Admin contract and capability model

**Objective**: renderer-independent admin contract exposing resource metadata, field metadata, permissions, capabilities, and tenant context explicitly for any UI client.

**Deliverables**
- `admin/contract.py`, `admin/capabilities.py`, `admin/navigation.py`
- `schemas/admin_contract.py`, `schemas/capabilities.py`, `schemas/navigation.py`
- `schemas/client_config.py`
- Endpoints: `/admin/context`, `/admin/navigation`, `/admin/capabilities`, `/admin/registry`, `/admin/registry/{model}/meta`, `/admin/{model}/lookup`, `/admin/client-config`, `/admin/compatibility`

**Hard requirements**
- Admin metadata must be explicit and renderer-independent
- Built-in UI and external clients must consume the same admin contract
- Protected, internal, and server-only fields must never appear in UI metadata
- Clients must not require ORM inspection, `__dict__`, model reflection, or hidden conventions
- Model metadata must distinguish list, detail, create, update, filter, and action presentation
- Tenant-scoped models clearly marked in metadata
- Capability metadata exposes allowed operations for current user and tenant context in UI-safe form
- Field metadata exposes readonly, required, nullable, default-present, sortable, filterable, searchable, and relation flags
- Relation fields expose enough metadata for generic selection and display without leaking backend internals
- Action metadata includes label, danger state, confirmation requirement, bulk/single applicability, and permission gating
- Admin context exposes current user, tenant context, impersonation state, enabled features, and visible navigation in UI-safe format
- Contract versioned; breaking changes increment the major version
- Additive changes (new optional fields) do not increment the major version; clients must ignore unknown fields

**Acceptance criteria**
- `/admin/context` returns authenticated admin context without exposing secrets
- `/admin/capabilities` correctly differs for superadmin, scoped user, impersonated user, and denied user
- Protected fields are absent from all admin contract responses
- Metadata is sufficient to render generic list, detail, create, update, filter, and action screens

---

### Built-in admin UI

**Objective**: optional lightweight built-in web admin UI consuming the admin contract as its single source of truth.

**Deliverables**
- `routers/admin_ui.py`
- `templates/admin/` (login, nav, list, detail, create, update, confirm_delete, break_glass)
- `static/admin/`
- `admin/ui_renderer.py`, `admin/ui_helpers.py`
- Renderer support matrix

**Hard requirements**
- Built-in UI is optional; disabling it must not affect API behavior
- Built-in UI consumes the admin contract rather than duplicating backend model logic
- Unsupported metadata-driven features degrade safely with explicit non-breaking fallback UI
- Built-in UI must not expose secrets, protected fields, or raw ORM internals
- Built-in UI respects tenant context, impersonation visibility, readonly fields, protected fields, filtering, search, ordering, and pagination
- Delete and dangerous actions require explicit confirmation UX where metadata marks them dangerous
- Built-in UI clearly shows impersonation state and tenant context
- Break-glass initiation requires reason capture and must not bypass readonly or protected-field rules
- Validation and authorization failures render clearly without leaking backend internals
- Personal UI preferences (visible columns, sorting, density, navigation favorites) can be saved and restored without overriding server-enforced permissions

**Acceptance criteria**
- Admin login shell loads successfully
- Registered models appear in built-in navigation according to user visibility rules
- Generic list, detail, create, and update pages render from contract metadata
- Basic search, filter, ordering, and pagination work
- Protected fields are absent from all rendered pages
- Unsupported advanced features render a safe fallback state

---

### Audit, impersonation, break-glass, logout

**Objective**: audit logging, impersonation, break-glass editing, token blacklist logout, revocation flows.

**Deliverables**
- `models/audit_log.py`, `models/impersonation_log.py`
- `token_blacklist.py`
- Extended `dependencies.py` with blacklist and impersonation state
- Extended `routers/auth.py` with logout and refresh restrictions
- Extended tenant router with impersonation and revoke flows
- `routers/audit.py`, `routers/break_glass.py`
- `middleware/audit.py`

**Hard requirements**
- Logout must revoke the current access token by JTI
- Refresh must reject non-renewable tokens
- Impersonation tokens must be non-renewable and rejected by superadmin-only routes
- Audit middleware must never break the main request flow if audit write fails
- Break-glass must require a meaningful reason and write both master and tenant audit records
- Break-glass must reject protected and read-only fields
- Impersonation revocation must blacklist the impersonation token

**Acceptance criteria**
- Logout invalidates the current access token
- Impersonation token cannot be refreshed
- Impersonation token is rejected by direct superadmin-only routes
- Audit log records method, path, status, user, tenant, action, and object identifier
- Break-glass returns evidence of dual audit writes
- Short or missing break-glass reason returns 422
- Protected or readonly break-glass edits are rejected

**Required e2e flows**
1. login → logout → same access token rejected
2. tenant creation → tenant migration → impersonation → scoped action → revoke → scoped token rejected
3. break-glass edit → dual audit presence verified

---

### Fine-grained authorization

**Objective**: policy engine for resource, field, action, and record-level authorization beyond simple role checks.

**Deliverables**
- `authz/policy_engine.py`, `authz/rules.py`
- `schemas/policy.py`
- Extended `dependencies.py` with policy evaluation helpers
- Extended `admin/capabilities.py` and `admin/contract.py`

**Hard requirements**
- Policy enforcement supports more than route-level allow or deny
- Field visibility and field editability are separately representable and enforceable
- Record-level restrictions are enforced server-side — not relying on UI hiding
- Superadmin bypass remains explicit and narrowly controlled
- Impersonation tokens must not gain access beyond the impersonated identity
- Policy failures return explicit authorization errors without leaking hidden resource details
- Tenant-scoped access is not weakened by policy abstractions

**Acceptance criteria**
- A user can view but not edit a field on the same resource
- A user can edit some records but not others for the same model
- Denied actions are absent or disabled in capability metadata and rejected server-side if attempted directly
- Record-level restrictions are enforced in list, detail, update, delete, and action flows
- Protected fields remain absent regardless of policy grants

---

### Workflows and approval flows

**Objective**: reviewable, staged, and reversible administrative changes for higher-risk operations.

**Deliverables**
- `models/change_request.py`, `schemas/change_request.py`
- `services/workflow.py`
- `routers/workflow.py`
- Extended metadata and capability exposure for workflow
- Built-in UI support for review, approve, reject, and revert flows

**Hard requirements**
- Workflow support is optional per model or action, driven by explicit admin metadata (`requires_approval`)
- Reviewed changes record requester, reviewer, timestamps, decision, and reason
- Approval and rejection actions are policy-gated and auditable
- Draft changes must not bypass existing validation, readonly, protected-field, or tenant restrictions
- Revert or rollback support limited to safe, explicit, auditable paths
- Built-in UI and external clients discover workflow requirements through metadata or capability endpoints

**Acceptance criteria**
- A configured model or action requires approval before applying a state-changing operation
- A reviewer can approve or reject a pending change with an auditable reason
- A rejected change is not applied
- A reverted change creates a new auditable event rather than mutating history invisibly
- Metadata and capability responses expose whether approval or revert flows are available
- Models without workflow enabled continue to behave normally

---

### Billing

**Objective**: tenant billing — track usage and generate invoices per tenant.

**Deliverables**
- `models/billing.py` (invoice, line item, billing period)
- `schemas/billing.py`
- `services/billing.py`
- `routers/billing.py`
- Tenant billing admin registration

**Hard requirements**
- Billing records are strictly tenant-scoped and never leak across tenant boundaries
- Billing data must not expose other tenants' data in any response
- Billing admin is superadmin-only by default
- Invoice generation must be idempotent per billing period per tenant
- Billing status (outstanding, paid, overdue) must be representable and queryable
- Protected billing fields (payment tokens, external processor IDs) must follow protected-field rules
- Billing must work when `MULTI_TENANT=false` (single-tenant billing against the shared context)

**Acceptance criteria**
- Invoice can be created, read, and listed for a tenant
- Invoice line items can be added and are scoped to the parent invoice
- Cross-tenant data is never visible in billing responses
- Duplicate invoice for the same billing period returns 409
- Billing status transitions are auditable

---

### Security hardening and sessions

**Objective**: stronger session controls, step-up security, rate limits, and secure headers.

**Deliverables**
- Extended `routers/auth.py`
- `schemas/session.py`
- `services/session_security.py`
- `middleware/security_headers.py`
- `middleware/rate_limit.py`
- Optional session listing and revocation endpoints
- Optional step-up auth challenge flow

**Hard requirements**
- Critical admin actions can require recent authentication or step-up proof
- Built-in UI security model handles CSRF, cookie/session strategy, or token transport without ambiguity
- Rate limiting or brute-force mitigation protects login and abuse-prone endpoints
- Session handling supports explicit revocation beyond single-token logout
- Admin responses include safe security headers appropriate for the built-in UI delivery model
- Security hardening must not weaken impersonation restrictions, superadmin protections, or policy enforcement

**Acceptance criteria**
- At least one critical action path requires recent-auth or step-up enforcement
- Login abuse protection rejects repeated invalid attempts according to configured policy
- Active admin sessions can be listed and selectively revoked
- Built-in UI responses include configured security headers

---

### Observability

**Objective**: metrics and alert-ready telemetry for admin operations.

**Deliverables**
- `observability/admin_metrics.py`
- Metrics for: admin request counts, failures, latencies, action outcomes, audit-write failures, contract-version usage

**Hard requirements**
- Metrics exist for admin request counts, failures, latencies, action outcomes, and audit-write failures
- Telemetry should distinguish built-in UI and external clients where safely derivable
- Telemetry must not expose secrets, token internals, or protected field content
- Operational failures in metrics must not change primary endpoint success semantics

**Acceptance criteria**
- Metrics capture admin request, action, and client-contract usage counters without changing functional behavior
- Audit write failure metrics are emitted when audit persistence fails

---

### External client contract stabilization

**Objective**: version and stabilize the admin contract so a separate external client (e.g. Flutter) can fully consume it without built-in-UI assumptions.

**Deliverables**
- Explicit contract versioning for admin metadata endpoints
- `/admin/client-config` endpoint
- Extended `/admin/capabilities` for renderer/client feature flags
- Relation lookup endpoints for generic async selection flows
- Compatibility documentation for baseline, advanced, and client-specific flows
- Explicit deprecation and compatibility policy

**Hard requirements**
- Built-in UI and external clients must use the same core admin contract
- External clients must not require ORM inspection, server internals, or built-in-UI-specific assumptions
- Contract changes affecting clients must be versioned or compatibility-scoped
- Relation fields expose enough metadata for generic label resolution, async lookup, pagination, and tenant-safe option retrieval
- Disabling built-in UI must not affect external-client support
- Contract snapshot tests are usable as release gates

**Acceptance criteria**
- `/admin/context`, `/admin/navigation`, `/admin/capabilities`, registry/model metadata, relation lookup endpoints, and `/admin/client-config` are sufficient for an external client to render generic admin flows
- No protected fields appear in any external-client-facing metadata
- Relation lookups function for at least one representative searchable relation and one paginated relation
- Deprecation rules are documented and testable

---

### Package structure and extension architecture

**Objective**: maintainable, adoptable Python package with clean core/extension/parked boundaries, typed config, and developer tooling.

**Package layers**

```
coreAdmin_api/
  core/            typed config model
  admin/           registry, schema builder, serializer, contract, UI
  models/          shared SQLAlchemy models
  schemas/         Pydantic schemas
  routers/         core API routers
  middleware/      error, logging, tenant, audit, security, rate-limit
  authz/           policy engine
  services/        workflow, billing, session security
  observability/   metrics
  extensions/
    jobs/          (if retained)
    import_export/ (if retained)
    workflows/
    enterprise_client/
  parked/
    billing/       (promote to billing/ when ready)
    usage_metering/
    seat_limits/
    white_labeling/
    scim_saml/
    flutter_offline_cache/
  experimental/
```

**`CoreAdminConfig`**

```python
CoreAdminConfig(
    enable_builtin_ui=True,
    enable_multi_tenant=False,
    enable_basic_audit=True,
    enable_workflows=False,
    enable_billing=False,
    extensions=[...],
)
```

- Defaults produce a minimal core installation
- Disabled features do not mount routers, expose metadata, or import heavy dependencies
- Config validation fails early for inconsistent combinations
- `/admin/context` and `/admin/capabilities` reflect enabled features

**`ExtensionBase` interface**

Each extension declares: routers to mount, models and migration metadata, admin registry contributions, capability metadata, settings schema, startup checks, health checks.

- Extension loading order is deterministic (registration order)
- Extension failure must fail loudly at startup unless marked optional
- Extensions must not bypass protected-field filtering, tenant isolation, policy checks, or audit rules

**Optional extras (`pyproject.toml`)**

| Extra            | Installs                                 |
|------------------|------------------------------------------|
| `[ui]`           | built-in admin UI deps (jinja2, aiofiles)|
| `[postgres]`     | asyncpg, alembic                         |
| `[dev]`          | pytest, httpx, aiosqlite                 |

**CLI commands**

Required:
- `coreadmin init` — create minimal working app scaffold
- `coreadmin create-superadmin`
- `coreadmin inspect-registry` — report models, fields, actions, protected fields, contract readiness
- `coreadmin doctor` — check DB, migrations, extensions, missing deps, contract generation
- `coreadmin db check`
- `coreadmin db upgrade [--env shared|tenant]`
- `coreadmin tenant migrate <slug>`
- `coreadmin tenant upgrade --all`
- `coreadmin extensions list`
- `coreadmin extensions check`

Optional:
- `coreadmin config show`
- `coreadmin routes list`
- `coreadmin contract snapshot`

**Hard requirements**
- Core package import must remain lightweight
- Disabled optional features must not import heavy dependencies or mount routes
- Parked modules must not be imported by default
- Extension architecture must preserve all security invariants
- Configuration must be typed, validated, and reflected in admin metadata
- CLI commands must be safe to run repeatedly

**Acceptance criteria**
- A new user can install the core package and run the minimal example without billing, artifact, or Flutter dependencies
- `coreadmin init` creates a minimal working app skeleton
- `coreadmin inspect-registry` reports registered models with fields, actions, protected fields, and contract readiness
- `coreadmin doctor` reports DB state, migration state, enabled extensions, missing dependencies, and contract generation status
- Optional extensions can be enabled explicitly and are absent when disabled
- Parked modules are not imported during normal app startup
- Built-in UI baseline still works when enabled
- API-only mode still works when built-in UI is disabled
- Multi-tenancy remains optional and explicit

---

## Test requirements

### Layers required per feature area

| Feature area                    | fast | integration | e2e |
|---------------------------------|------|-------------|-----|
| Foundation                      | ✓    | optional    |     |
| User and role management        | ✓    | recommended |     |
| Multi-tenancy                   | ✓    | required    |     |
| Registry-driven admin CRUD      | ✓    | required    |     |
| Admin contract and capabilities | ✓    | required    |     |
| Built-in admin UI               | ✓    | required    | ✓   |
| Audit, impersonation, break-glass | ✓  | required    | ✓   |
| Fine-grained authorization      | ✓    | required    | ✓   |
| Workflows                       | ✓    | required    | ✓   |
| Billing                         | ✓    | required    |     |
| Security hardening              | ✓    | required    | ✓   |
| Observability                   | ✓    | required    |     |
| External client contract        | ✓    | required    | recommended |
| Package structure               | ✓    | required    | ✓   |

### Cross-cutting regression rules

- Existing auth, tenant, audit, and admin contract behavior must remain unchanged when adding new features
- Protected-field filtering must be re-verified after every change to registry or contract logic
- Tenant isolation must be re-verified after every change to DB session or middleware logic
- Contract snapshot tests must be usable as release gates for at least two representative models under at least three contexts (superadmin, scoped user, tenant-scoped user)

---

## Flutter enterprise UI boundary

The Flutter enterprise UI should be a **separate repository** — different toolchain, release cadence, testing stack, and distribution model.

**What the Python package must provide for Flutter**
- Stable versioned admin contract
- `/admin/context`, `/admin/navigation`, `/admin/capabilities`, `/admin/client-config`
- Registry/model metadata endpoints
- Relation lookup endpoints
- Action metadata and execution endpoints
- Workflow metadata where the workflow extension is enabled
- Consistent error response formats
- Contract snapshot fixtures for representative contexts

**What the Flutter app needs (outside this repo)**
- App shell with responsive layout
- Authenticated routing and route guards
- Generated or hand-written API client for the versioned admin contract
- Tenant switcher and context handling
- Generic metadata-driven list/detail/create/update screens
- Relation selector widgets
- Dangerous-action confirmation UX
- Validation and authorization error rendering
- Audit log views
- Workflow inbox where the workflow extension is enabled

---

## Minimal meta-prompt template

Use this when adding a new feature.

### Objective
State the single main goal.

### Deliverables
List exact files and explicit extensions.

### Hard requirements
List the non-negotiable functional and security rules.

### Acceptance criteria
Use measurable statements only.

### Tests
State which of fast, integration, and e2e are required.
State what must be covered and what regressions must remain green.

### Build order
Keep it short and execution-oriented.
