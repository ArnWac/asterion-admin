# adminfoundry — Schema-per-Tenant v1 Execution Plan for Claude Code

## Ziel

Dieser Plan ersetzt die alte row-level-orientierte Refactor-Roadmap.

`adminfoundry` soll als **clean-break schema-per-tenant v1** stabilisiert werden.

Das Ziel ist nicht, alte row-level-Mechaniken weiter zu refactoren, sondern sie kontrolliert zu entfernen.

Repository:

```text
https://github.com/ArnWac/adminfoundry
```

---

## Harte Architekturentscheidung

**Decided 2026-05-17: clean-break schema-per-tenant.**

Das bedeutet:

```text
public schema
  = globale Identität, Tenant Registry, Membership/Auth Gate, Audit, Permission Catalog

tenant schema
  = tenant-lokale Rollen, tenant-lokale Role-Permissions, tenant-lokale Businessdaten
```

Tenant-lokale Business-Tabellen haben **kein `tenant_id`**.

Tenant-Isolation entsteht durch:

```text
1. Tenant aus Request auflösen
2. User authentifizieren
3. aktive TenantMembership im public schema prüfen
4. tenant schema sicher aktivieren
5. tenant-lokales RBAC prüfen
6. Businessdaten nur im aktiven tenant schema lesen/schreiben
```

Nicht mehr durch:

```text
WHERE model.tenant_id = current_tenant.id
```

---

## Zielmodell

```text
User
  = globale Identität
  = public.users

Tenant
  = globale Tenant Registry
  = public.tenants
  = enthält schema_name

TenantMembership
  = globales User ↔ Tenant Access Gate
  = public.tenant_memberships
  = beantwortet: Darf dieser User grundsätzlich in diesen Tenant?

PermissionCatalog
  = globaler Katalog möglicher Permission Keys
  = public.permission_catalog

TenantRole
  = tenant-lokale Rolle
  = tenant_schema.tenant_roles oder tenant_schema.roles

TenantRolePermission
  = tenant-lokale Zuordnung Role → Permission Key
  = tenant_schema.tenant_role_permissions oder tenant_schema.role_permissions

TenantMembershipRole
  = tenant-lokale Zuordnung Membership → Role
  = tenant_schema.tenant_membership_roles oder tenant_schema.membership_roles

Business Models
  = tenant-lokale Tabellen
  = kein tenant_id
```

---

## Zentrale Design-Regel

`TenantMembership` lebt im globalen Schema.

Tenant-lokale Rollen sollten **nicht** als normale dauerhafte ORM-Relationship an `TenantMembership` modelliert werden, weil sie in dynamischen Tenant-Schemas leben.

Stattdessen soll ein expliziter Auth-Kontext geladen werden:

```python
@dataclass(slots=True)
class TenantAuthContext:
    tenant: Tenant
    membership: TenantMembership
    roles: list[TenantRole]
    permission_keys: set[str]
```

Dieser Kontext wird pro Request geladen und auf `request.state` abgelegt:

```python
request.state.tenant_auth
```

Empfohlene Zugriffsmuster:

```python
tenant_auth = request.state.tenant_auth
tenant_auth.membership
tenant_auth.roles
tenant_auth.permission_keys
```

Nicht:

```python
membership.roles
membership.tenant_roles
user.roles in tenant context
```

---

## Globale Regeln für Claude Code

Diese Regeln gelten für jede Phase.

```text
- This is pre-v1.
- No backwards compatibility shims.
- Do not preserve row-level tenancy behavior.
- Do not add compatibility wrappers for tenant_id.
- Do not keep deprecated row-level code “just in case”.
- Do not create an obsolete/ folder.
- Use git history as archive.
- Do not use UI visibility as authorization.
- Do not use User.roles for tenant-context authorization.
- Do not reintroduce _tenant_filter.
- Do not reintroduce _inject_tenant_id.
- Do not rely on tenant_id columns in tenant-local business models.
- Keep changes small and reviewable.
- Prefer deleting obsolete code over adapting it.
- Production LOC should decrease during cleanup phases.
- If a phase reveals a larger conflict, stop and report it.
```

---

## Aktueller Stand laut Refactor-Notizen

```text
DONE   Phase A — SET LOCAL search_path fix in schema_strategy.py
DONE   Phase B — TenantBase + PermissionCatalog model
DONE   Phase C — TenantRole / TenantRolePermission / TenantMembershipRole + migration 0001
DONE   Phase D — bootstrap_tenant service + CLI + migrate_tenant endpoint

NEXT   Phase 1 — Freeze and verify current schema-per-tenant baseline
NEXT   Phase 2 — TenantAuthContext
NEXT   Phase 3 — PolicyEngine / role_caps / helpers use TenantAuthContext
NEXT   Phase 4 — Permission Matrix uses tenant-local RBAC
NEXT   Phase 5 — CRUD uses tenant schema only, no tenant_id mechanics
NEXT   Phase 6 — Remove row-level artifacts
NEXT   Phase 7 — PostgreSQL schema isolation tests
TODO   Phase 8 — Thin admin routes
TODO   Phase 9 — Documentation
TODO   Phase 10 — Optional TenantMembership → TenantUser rename
```

---

# Phase 1 — Freeze and Verify Schema-per-Tenant Baseline

## Goal

Verify the current implementation state without adding new architecture.

This phase is read-mostly. It should identify whether the claimed completed schema-per-tenant pieces are actually present and coherent.

## Scope

Inspect:

```text
adminfoundry/tenancy/schema_strategy.py
adminfoundry/database.py
adminfoundry/models/base.py
adminfoundry/models/tenant.py
adminfoundry/models/tenant_membership.py
adminfoundry/models/permission_catalog.py
adminfoundry/models/tenant_role.py
adminfoundry/services/bootstrap_tenant.py
adminfoundry/cli/*
adminfoundry/routers/*
migrations/*
tests/*
```

## Claude Code Prompt

```md
# Phase 1 — Verify Schema-per-Tenant Baseline

You are working in the adminfoundry repository.

Do not perform broad refactoring.

Goal:
Verify the current schema-per-tenant baseline and identify inconsistencies.

Architecture decision:
We are doing a clean-break schema-per-tenant v1.
Do not preserve row-level tenancy.

Check the following:

1. SET LOCAL search_path behavior
   - Is tenant schema activation transaction-local?
   - Can search_path leak through pooled connections?
   - Is schema_name loaded from public.tenants and validated?

2. Global vs tenant model split
   - Are User, Tenant, TenantMembership, Audit, PermissionCatalog global?
   - Are TenantRole, TenantRolePermission, TenantMembershipRole tenant-local?
   - Are tenant business models based on TenantBase or equivalent?

3. Tenant bootstrap
   - Does bootstrap_tenant create schema?
   - Does it run tenant migrations?
   - Does it seed default roles?
   - Does it assign owner role to the initial membership?
   - Is it idempotent or safely failing?

4. Remaining row-level artifacts
   - Find _tenant_filter
   - Find _inject_tenant_id
   - Find model.tenant_id usage in tenant-local business models
   - Find setdefault("tenant_id")
   - Find role.tenant_id checks
   - Find public membership_roles/user_roles used for tenant auth

5. Tenant auth gaps
   - Find User.roles in tenant context
   - Find get_current_user without tenant auth in tenant-scoped routes
   - Find PolicyEngine role usage
   - Find Permission Matrix role usage

Output:
- Baseline status table
- Remaining blockers
- Files that must be changed in Phases 2-7
- Do not edit files unless a tiny typo fix is unavoidable
```

## Acceptance Criteria

```text
- Current schema-per-tenant baseline is documented.
- All remaining row-level artifacts are listed.
- All tenant-auth gaps are listed.
- No broad production changes.
```

## Stop Conditions

Stop if the codebase still fundamentally depends on tenant_id for tenant-local models. Report required rollback or rewrite points.

---

# Phase 2 — Introduce TenantAuthContext

## Goal

Create a single request-level tenant authorization context that combines:

```text
resolved tenant
+
public TenantMembership
+
tenant-local roles
+
tenant-local permission keys
```

This is the core replacement for old row-level role/membership handling.

## Scope

Likely files:

```text
adminfoundry/tenancy/dependencies.py
adminfoundry/tenancy/context.py
adminfoundry/authz/role_caps.py
adminfoundry/database.py
tests/tenancy/*
```

## Claude Code Prompt

```md
# Phase 2 — Introduce TenantAuthContext

You are working in the adminfoundry repository.

Goal:
Introduce a TenantAuthContext for schema-per-tenant authorization.

Hard architecture:
- User, Tenant, TenantMembership live in public schema.
- TenantRole, TenantRolePermission, TenantMembershipRole live in tenant schema.
- TenantMembership is the global access gate.
- Tenant-local roles are loaded after membership succeeds and after tenant schema is safely activated.
- Do not model tenant-local roles as a permanent public-schema relationship on TenantMembership.
- Do not use User.roles in tenant context.

Implement or finalize:

```python
@dataclass(slots=True)
class TenantAuthContext:
    tenant: Tenant
    membership: TenantMembership
    roles: list[TenantRole]
    permission_keys: set[str]
```

Update `require_tenant_membership` or create a new dependency:

```python
async def require_tenant_auth_context(...) -> TenantAuthContext:
    ...
```

Required flow:

1. Read `request.state.tenant`.
2. Authenticate current user.
3. Query public.tenant_memberships:
   - user_id == current_user.id
   - tenant_id == tenant.id
   - active/status allows access
4. Reject missing/inactive membership with 403.
5. Activate tenant schema using safe SET LOCAL search_path or existing schema strategy.
6. Query tenant-local TenantMembershipRole, TenantRole, TenantRolePermission.
7. Build TenantAuthContext.
8. Store it:
   - `request.state.tenant_auth = tenant_auth`
9. Return it.

Permission loading:
- permission_keys should be a set[str]
- role names should be loaded from tenant schema
- no public role.tenant_id checks

Superadmin:
- Keep explicit superadmin behavior.
- Do not let superadmin accidentally bypass tenant schema logic in tenant routes unless explicitly designed.
- Impersonation tenant mismatch must reject.

Tests:
- require_tenant_auth_context rejects missing membership
- inactive membership rejects
- valid membership loads tenant-local roles
- valid membership loads tenant-local permission keys
- User.roles are ignored in tenant context
- tenant schema is activated before tenant-local RBAC queries

Do not touch CRUD behavior in this phase except to import/use the new context if required.
```

## Acceptance Criteria

```text
- TenantAuthContext exists.
- TenantAuthContext is request-scoped.
- Tenant-local roles/permissions are loaded from active tenant schema.
- User.roles are not used for tenant authorization.
- Missing/inactive membership returns 403.
- Tests cover context creation.
```

## Stop Conditions

Stop if tenant-local role models cannot be queried safely after schema activation. Report the schema/session issue.

---

# Phase 3 — PolicyEngine, role_caps, and Helpers Use TenantAuthContext

## Goal

Make all tenant-context authorization decisions use `TenantAuthContext`, not `User.roles`, not public `role.tenant_id`, and not old membership role relationships.

## Scope

Likely files:

```text
adminfoundry/authz/policy_engine.py
adminfoundry/authz/role_caps.py
adminfoundry/admin/_helpers.py
adminfoundry/admin/routes/contract.py
adminfoundry/admin/navigation.py
tests/authz/*
tests/admin/*
```

## Claude Code Prompt

```md
# Phase 3 — Use TenantAuthContext in PolicyEngine and Helpers

You are working in the adminfoundry repository.

Goal:
All tenant-context authorization must use TenantAuthContext.

Hard rules:
- Do not use User.roles in tenant context.
- Do not use membership.roles in tenant context if that refers to public-schema relationships.
- Do not use role.tenant_id checks for tenant-local RBAC.
- Do not use public membership_roles for tenant RBAC.
- Use tenant_auth.roles and tenant_auth.permission_keys.

Tasks:

1. Update PolicyEngine:
   - Accept tenant_auth or permission_keys where tenant-context decisions are made.
   - Field policies use tenant_auth.permission_keys or tenant_auth role names.
   - Action policies use tenant_auth.permission_keys or tenant_auth role names.
   - effective_model_caps uses tenant_auth.permission_keys.
   - record access hooks receive tenant_auth if needed.
   - No silent fallback to User.roles in tenant context.

2. Update role_caps.py:
   - Query tenant-local role_permissions if it still queries DB.
   - Prefer using tenant_auth.permission_keys when already loaded.
   - Remove public role.tenant_id logic for tenant permissions.

3. Update admin helpers:
   - _check_model_access uses tenant_auth.
   - _enforce_method_caps uses tenant_auth.
   - tenant_admin checks use tenant_auth roles/permissions.
   - superadmin/root behavior stays explicit.

4. Update navigation/contract/capabilities:
   - Use TenantAuthContext.
   - Navigation visibility must reflect backend auth but not replace it.

Tests:
- global User.roles tenant_admin does not grant tenant caps
- tenant-local admin role grants tenant caps
- policy engine reads tenant_auth permission keys
- role_caps does not query public role.tenant_id
- navigation uses tenant_auth consistently

Do not modify CRUD storage mechanics in this phase.
```

## Acceptance Criteria

```text
- Tenant-context PolicyEngine no longer reads User.roles.
- role_caps no longer depends on public tenant-scoped roles.
- helpers use TenantAuthContext.
- navigation/capability endpoints use TenantAuthContext.
- Tests pass.
```

## Stop Conditions

Stop if PolicyEngine API becomes too broad. Report proposed minimal interface.

---

# Phase 4 — Permission Matrix Uses Tenant-Local RBAC

## Goal

Move Permission Matrix behavior to schema-per-tenant RBAC.

Permission Matrix should read/write tenant-local roles and role permissions in the active tenant schema.

## Scope

Likely files:

```text
adminfoundry/admin/routes/permissions.py
adminfoundry/authz/role_caps.py
adminfoundry/models/tenant_role.py
tests/admin/test_permissions*
```

## Claude Code Prompt

```md
# Phase 4 — Permission Matrix on Tenant-Local RBAC

You are working in the adminfoundry repository.

Goal:
Permission Matrix must use tenant-local RBAC tables in the active tenant schema.

Hard rules:
- Permission Matrix tenant routes require TenantAuthContext.
- Tenant-admin checks use tenant_auth roles/permissions.
- Do not use User.roles for tenant-admin checks.
- Do not use public role.tenant_id.
- Do not read/write public role_permissions for tenant-local RBAC.
- role_id belongs to the active tenant schema by construction.

Tasks:

1. Inspect permission matrix routes.
2. Ensure every tenant-scoped permission route depends on TenantAuthContext.
3. Load roles from tenant schema only.
4. Load/update role_permissions in tenant schema only.
5. Validate permission_key against public PermissionCatalog if implemented.
6. Preserve explicit superadmin/root behavior separately.
7. Remove or isolate old public-RBAC permission matrix paths.
8. Do not create compatibility paths.

Tests:
- permission matrix requires active membership
- tenant-local admin can view/update tenant-local permissions
- User.roles tenant_admin cannot access tenant permission matrix
- role_id from another tenant schema is not accessible
- permission keys must exist in PermissionCatalog if validation is implemented
- tenant A permission changes do not affect tenant B

Run targeted permission tests.
```

## Acceptance Criteria

```text
- Permission Matrix uses tenant-local role tables.
- Permission Matrix is membership-gated.
- Tenant-admin checks use TenantAuthContext.
- Tenant A RBAC changes cannot affect Tenant B.
- Tests pass.
```

## Stop Conditions

Stop if old and new Permission Matrix paths cannot coexist safely. Recommend deleting old path.

---

# Phase 5 — CRUD Uses Tenant Schema Only

## Goal

CRUD must rely on the active tenant schema for tenant-local models.

Remove row-level assumptions from CRUD behavior.

## Scope

Likely files:

```text
adminfoundry/admin/routes/crud.py
adminfoundry/admin/_helpers.py
adminfoundry/admin/routes/import_export.py
adminfoundry/database.py
tests/admin/*
```

## Claude Code Prompt

```md
# Phase 5 — CRUD Uses Tenant Schema Only

You are working in the adminfoundry repository.

Goal:
Tenant-local CRUD must operate in the active tenant schema and must not use tenant_id mechanics.

Hard architecture:
- Tenant-local business models have no tenant_id.
- Tenant isolation is provided by safe tenant schema activation.
- There is no _tenant_filter in schema-per-tenant v1.
- There is no _inject_tenant_id in schema-per-tenant v1.
- Client-supplied tenant_id must be ignored/rejected if it appears for tenant-local models.
- Do not preserve row-level compatibility.

Tasks:

1. Inspect CRUD list/detail/create/update/delete/restore/hard-delete/bulk/import/export paths.
2. Ensure tenant-scoped routes require TenantAuthContext.
3. Ensure tenant-local queries run using the tenant schema session/search_path.
4. Remove tenant_id injection logic from create/import.
5. Remove tenant_id filtering logic from list/detail/update/delete/bulk.
6. Reject tenant_id field in tenant-local payloads if protected-field validation does not already remove it.
7. Ensure global/root models still use public/global schema.
8. Ensure import/export use active tenant schema for tenant-local models.
9. Avoid broad route restructuring unless required.

Tests:
- create writes to active tenant schema
- list reads only active tenant schema
- detail for same UUID in different schemas resolves only active tenant's object
- update affects only active tenant schema
- delete affects only active tenant schema
- bulk action affects only active tenant schema
- import writes only active tenant schema
- tenant_id payload is rejected or ignored consistently
- no _tenant_filter required for tenant-local models

Run targeted CRUD tests.
```

## Acceptance Criteria

```text
- CRUD no longer depends on tenant_id for tenant-local models.
- TenantAuthContext is required for tenant-local CRUD.
- Queries run in active tenant schema.
- tenant_id injection is removed.
- tenant_id filters are removed.
- Tests pass.
```

## Stop Conditions

Stop if global and tenant-local models cannot be distinguished reliably. Report required model classification design.

---

# Phase 6 — Remove Row-Level Artifacts

## Goal

Delete obsolete row-level tenancy code.

This phase should reduce code.

## Scope

Likely artifacts:

```text
_tenant_filter
_inject_tenant_id_if_needed
tenant_id on tenant-local business models
public membership_roles for tenant RBAC
public role.tenant_id logic for tenant RBAC
public role_permissions used for tenant RBAC
tests that assert row-level tenant_id behavior
docs that claim row-level is current strategy
```

## Claude Code Prompt

```md
# Phase 6 — Remove Row-Level Tenancy Artifacts

You are working in the adminfoundry repository.

Goal:
Delete row-level tenancy artifacts after schema-per-tenant replacements are implemented and tested.

This is a reduction phase.
Production LOC should decrease.

Hard rules:
- Do not create compatibility wrappers.
- Do not move old code to obsolete/.
- Use git history as archive.
- Do not keep row-level code “for later”.
- Do not delete migrations blindly if they are part of the current migration chain; handle migration strategy explicitly.

Candidates to remove:
- _tenant_filter
- _inject_tenant_id_if_needed
- setdefault("tenant_id")
- tenant_id from tenant-local business example models
- tenant_id-based tenant filters in CRUD
- public membership_roles used for tenant RBAC
- public role.tenant_id tenant auth logic
- public role_permissions used for tenant RBAC
- tests that only verify old row-level behavior
- docs that describe row-level as current strategy

Before deleting each candidate:
1. Show where it is used.
2. Show replacement path.
3. Show test coverage.
4. Delete only if safe.

After deletion:
- run targeted tests
- run import checks
- run ruff if configured

Output:
- deleted functions/files
- retained items and why
- tests run
```

## Acceptance Criteria

```text
- Row-level tenant artifacts are removed.
- No obsolete folder is created.
- No compatibility wrappers are added.
- Tests pass.
- Codebase is smaller or simpler.
```

## Stop Conditions

Stop if a row-level artifact is still required by an active code path. Report the blocking path.

---

# Phase 7 — PostgreSQL Schema Isolation Tests

## Goal

Add integration tests that prove schema-per-tenant isolation works with PostgreSQL.

SQLite-only tests are not enough for search_path/schema behavior.

## Scope

Likely files:

```text
tests/integration/postgres/*
tests/tenancy/*
pytest config
docker compose test service if present
```

## Claude Code Prompt

```md
# Phase 7 — PostgreSQL Schema Isolation Tests

You are working in the adminfoundry repository.

Goal:
Add PostgreSQL integration tests for schema-per-tenant isolation.

Do not rely only on SQLite for schema behavior.

Test requirements:

1. Tenant schema activation
   - SET LOCAL search_path is applied inside transaction.
   - search_path does not leak between requests/sessions.
   - schema_name comes from public.tenants, not request input.

2. Tenant-local business isolation
   - Tenant A and Tenant B have same table names.
   - Same UUID/object ID in different schemas does not cross-resolve.
   - CRUD in Tenant A cannot read/write Tenant B.

3. Tenant-local RBAC isolation
   - Tenant A role "admin" is independent from Tenant B role "admin".
   - Tenant A role permissions do not affect Tenant B.
   - membership role assignment in Tenant A does not affect Tenant B.

4. Tenant auth pipeline
   - no membership => 403 before tenant-local business access
   - inactive membership => 403
   - valid membership loads tenant-local roles/permission_keys
   - refreshed token cannot bypass tenant membership check

5. Connection pooling
   - sequential requests to different tenants use correct schema
   - concurrent or simulated pooled sessions do not leak schema

If test infra is missing:
- add minimal documented PostgreSQL test setup
- mark integration tests separately
- do not silently skip critical tests without clear reason

Output:
- tests added
- how to run them
- any limitations
```

## Acceptance Criteria

```text
- PostgreSQL integration tests exist.
- Schema isolation is proven.
- search_path leakage is tested.
- Tenant-local RBAC isolation is tested.
- Tests are documented.
```

## Stop Conditions

Stop if test infrastructure cannot support PostgreSQL. Provide a minimal Docker-based proposal.

---

# Phase 8 — Thin Admin Routes After Security Stabilizes

## Goal

Only after Phases 2-7 are green, reduce route complexity without changing behavior.

## Scope

Likely files:

```text
adminfoundry/admin/routes/crud.py
adminfoundry/admin/routes/permissions.py
adminfoundry/admin/_helpers.py
adminfoundry/admin/services/*
```

## Claude Code Prompt

```md
# Phase 8 — Thin Admin Routes Without Behavior Change

You are working in the adminfoundry repository.

Precondition:
Schema-per-tenant security tests pass.

Goal:
Reduce complexity in admin route files without changing behavior.

Hard rules:
- No new features.
- No compatibility wrappers.
- No broad package restructure.
- Do not change endpoint paths.
- Do not change response schemas unless tests require a documented fix.
- Every extracted helper must replace duplicated or overly large code.

Tasks:

1. Identify large route functions and duplicated logic.
2. Extract cohesive helpers/services for:
   - tenant auth context retrieval
   - tenant-local object loading
   - serialization response assembly
   - import row processing
   - permission matrix mutations
3. Keep route handlers thin.
4. Run tests after each extraction group.
5. Do not mix cleanup with new schema behavior.

Suggested structure only if useful:

```text
adminfoundry/admin/services/
  crud_service.py
  import_service.py
  permission_service.py
```

Output:
- before/after structure
- helpers extracted
- files changed
- tests run
```

## Acceptance Criteria

```text
- Admin routes are thinner.
- Behavior unchanged.
- Tests pass.
- No unrelated modules touched.
```

## Stop Conditions

Stop if extraction requires semantic changes.

---

# Phase 9 — Documentation

## Goal

Update docs to reflect the new schema-per-tenant architecture.

## Scope

Docs and comments only.

## Claude Code Prompt

```md
# Phase 9 — Document Schema-per-Tenant v1

You are working in the adminfoundry repository.

Goal:
Update documentation so it matches the clean-break schema-per-tenant v1 architecture.

Document:

1. Schema split
   - public users
   - public tenants
   - public tenant_memberships
   - public permission_catalog
   - tenant-local roles
   - tenant-local role_permissions
   - tenant-local membership_roles
   - tenant-local business models

2. Request flow
   - resolve tenant
   - authenticate user
   - check public tenant membership
   - activate tenant schema with safe SET LOCAL search_path
   - load tenant-local RBAC
   - execute tenant-local business queries

3. TenantAuthContext
   - what it contains
   - where it is created
   - who uses it

4. Security invariants
   - UI navigation is not authorization
   - login alone is not sufficient
   - User.roles are not tenant roles
   - no tenant_id in tenant-local business models
   - no _tenant_filter in schema-per-tenant
   - search_path must not leak

5. Migration/bootstrap
   - global migrations vs tenant schema migrations
   - bootstrap_tenant behavior
   - default roles
   - owner assignment

6. Testing
   - SQLite unit tests
   - PostgreSQL schema isolation tests

Remove or update docs that still describe row-level tenancy as current behavior.

Do not add marketing language.
Keep docs implementation-oriented.
```

## Acceptance Criteria

```text
- Docs match implementation.
- Row-level tenancy is not described as current strategy.
- Security invariants are explicit.
- Bootstrap/migration model is clear.
```

---

# Phase 10 — Optional Rename TenantMembership to TenantUser

## Goal

Optionally rename `TenantMembership` to `TenantUser`.

This should happen only after schema-per-tenant architecture is stable.

## Rationale

`TenantUser` may better express:

```text
global User inside a Tenant
```

But `TenantMembership` is also acceptable if the terminology is already stable.

## Claude Code Prompt

```md
# Phase 10 — Optional Rename TenantMembership to TenantUser

You are working in the adminfoundry repository.

This phase is optional.

Precondition:
All schema-per-tenant auth and integration tests pass.

Goal:
Rename TenantMembership to TenantUser if the project chooses that terminology.

Rules:
- No behavior change.
- No compatibility alias unless explicitly requested.
- This is pre-v1.
- Keep migration implications explicit.
- Do not mix old and new terminology.

Tasks:

1. Rename model class if approved:
   - TenantMembership -> TenantUser

2. Rename table if approved:
   - tenant_memberships -> tenant_users

3. Rename related concepts:
   - TenantMembershipRole -> TenantUserRole if desired
   - tenant_membership_roles -> tenant_user_roles if desired

4. Update:
   - imports
   - tests
   - docs
   - CLI
   - bootstrap
   - migrations

5. Run full tenant/auth test suite.

Output:
- files changed
- migration impact
- remaining terminology references
```

## Acceptance Criteria

```text
- Terminology is consistent.
- Tests pass.
- No duplicate Membership/TenantUser concepts remain.
```

## Stop Conditions

Stop if migration rename is risky. Report manual options.

---

# Manual Review Checklist

After each Claude phase, manually check:

```text
- Did it reintroduce tenant_id mechanics?
- Did it reintroduce _tenant_filter?
- Did it reintroduce _inject_tenant_id?
- Did it use User.roles in tenant context?
- Did it add compatibility wrappers?
- Did it keep old row-level logic “just in case”?
- Did it touch unrelated modules?
- Did it weaken tests?
- Did it rely on UI visibility for authorization?
- Did it make search_path persistent instead of SET LOCAL?
- Did it query tenant-local RBAC before membership check?
- Did it mix public RBAC and tenant-local RBAC?
```

Useful commands:

```bash
pytest
ruff check .
mypy adminfoundry

rg "_tenant_filter" adminfoundry
rg "_inject_tenant_id|setdefault\\(\"tenant_id\"" adminfoundry
rg "tenant_id" adminfoundry/admin adminfoundry/authz adminfoundry/models
rg "user\\.roles" adminfoundry
rg "membership\\.roles" adminfoundry
rg "role\\.tenant_id" adminfoundry
rg "membership_roles" adminfoundry
rg "search_path" adminfoundry
rg "SET search_path|SET LOCAL" adminfoundry
```

Expected cleanup direction:

```text
- _tenant_filter should disappear.
- _inject_tenant_id should disappear.
- tenant_id should disappear from tenant-local business models.
- User.roles should not be used for tenant auth.
- public membership_roles should not be used for tenant RBAC.
- role.tenant_id should not be used for tenant-local RBAC.
```

---

# Recommended Execution Order

```text
Phase 1 — Freeze and verify schema-per-tenant baseline
Phase 2 — Introduce TenantAuthContext
Phase 3 — PolicyEngine / role_caps / helpers use TenantAuthContext
Phase 4 — Permission Matrix uses tenant-local RBAC
Phase 5 — CRUD uses tenant schema only
Phase 6 — Remove row-level artifacts
Phase 7 — PostgreSQL schema isolation tests
Phase 8 — Thin admin routes
Phase 9 — Documentation
Phase 10 — Optional TenantMembership → TenantUser rename
```

Do not start Phase 6 before Phases 2-5 are implemented and covered.

Do not start Phase 8 before schema isolation tests pass.

Do not start Phase 10 until all schema-per-tenant behavior is stable.

---

# One-Time Claude Instruction Before Starting

Use this before giving Claude any individual phase:

```md
We are no longer following the old row-level tenancy roadmap.

The current architecture decision is clean-break schema-per-tenant v1.

Ignore old instructions about:
- tenant_id injection
- _tenant_filter
- role.tenant_id tenant scoping
- public membership_roles for tenant RBAC
- row-level tenant isolation

Your task is to implement the current phase only.
Do not add compatibility wrappers.
Do not preserve old row-level behavior.
Prefer deleting obsolete code once replacement tests exist.
```
