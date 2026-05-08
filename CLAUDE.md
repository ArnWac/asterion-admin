# CLAUDE.md

This file provides guidance to Claude Code when working in this repository.

Primary goal: **make small, correct, well-tested changes with minimal token usage and strict scope control**.

---

## Project overview

**adminfoundry** is a FastAPI-based admin framework for Python 3.11+.

The intended direction is a batteries-included admin system that can generate CRUD APIs and an admin UI from declarative `ModelAdmin` registrations, with strong defaults for authentication, authorization, auditability, schema generation, and optional multi-tenancy.

Keep a clear distinction between implemented functionality and planned or experimental functionality. Do not assume that a feature exists only because it is mentioned in this file. Verify against the code before modifying or extending it.

### Current core concept

- Declarative `ModelAdmin` registration.
- Runtime generation of admin-oriented API contracts and CRUD routes.
- FastAPI backend with SQLAlchemy and Pydantic.
- Lightweight built-in admin UI as the baseline interface.
- Security-sensitive fields must be protected at the API/schema boundary.
- Optional multi-tenant direction, depending on current implementation state.

### Planned or potentially experimental areas

Treat these as features to verify before relying on them:

- Advanced RBAC / policy engine.
- Audit logging beyond basic events.
- Approval workflows.
- Import/export with artifacts.
- Jobs/background tasks.
- Billing, usage metering, seat limits.
- White labeling.
- SCIM/SAML or enterprise identity features.
- Flutter-based external admin UI.
- Offline cache or sync.

---

## Commands

Use the existing project commands when available. Prefer Makefile commands if they exist.

```bash
# Setup
pip install -e ".[dev]"
# or
make install

# Environment / services
docker compose up -d
cp .env.example .env

# Run
make dev

# Test
make test
pytest tests/ -q

# Targeted tests
pytest tests/test_auth.py -q

# Migrations, if configured
make migrate-shared
alembic -c alembic_shared.ini upgrade head

# CLI, if implemented
adminfoundry create-superadmin
adminfoundry doctor
```

Before using a command, check that it exists in the repository. Do not invent commands.

---

## Architecture orientation

Use this as a map, not as proof that every file or feature exists.

| Layer | Typical location / responsibility |
|---|---|
| Entry | FastAPI app creation, middleware stack, router mounting |
| Config | Pydantic settings, framework config objects |
| Admin framework | ModelAdmin contract, registry, router generation, schema builder, actions, capabilities |
| Auth / AuthZ | Authentication, JWT/session handling, role or policy checks |
| Routers | Domain routers such as auth, users, roles, tenants, audit, workflow, health, admin UI |
| Services | Session security, approval logic, domain services |
| Models | SQLAlchemy models such as User, Role, Tenant, AuditLog, token models |
| Schemas | Pydantic request/response contracts |
| Middleware | Audit, tenant resolution, rate limiting, security headers, request logging |
| Extensions | Optional extras such as jobs, import/export, billing |

---

## Admin framework conventions

Typical pattern:

```python
class ArticleAdmin(ModelAdmin):
    model = Article
    list_display = ["title", "published", "created_at"]
    search_fields = ["title"]
    filter_fields = ["published"]
    readonly_fields = ["id", "created_at", "updated_at"]

admin_site.register(ArticleAdmin())
```

Expected admin framework responsibilities, if implemented:

- `ModelAdmin` defines declarative admin configuration.
- Registry stores registered admin models.
- Contract generation exposes renderer-independent metadata for UI/API clients.
- Router generation creates admin CRUD routes.
- Schema builder creates read/write Pydantic schemas from SQLAlchemy models.
- Capabilities or authorization layer controls field and action permissions.
- Actions support single-record or bulk operations where implemented.

Do not assume behavior. Inspect the relevant module before changing it.

---

## Multi-tenancy guidance

Multi-tenancy is optional and must be treated carefully.

Before changing tenant behavior, verify:

- Whether multi-tenancy is currently enabled or only planned.
- Tenant resolution strategy, if any.
- Whether tenant isolation is schema-based, row-based, or not implemented.
- Whether migrations are shared, per-tenant, or both.
- Whether user, role, audit, and admin routes are tenant-scoped.

Tenant isolation bugs are security bugs. Add focused tests for any tenant-related change.

---

## Security invariants

These rules override convenience and speed.

- Never expose secrets, hashed secrets, token internals, database credentials, protected internal fields, or private keys in response schemas, logs, docs, or examples.
- Passwords, password hashes, PIN hashes, shared secrets, tenant salts, setup codes, QR bootstrap tokens, reset tokens, refresh tokens, and equivalent protected fields must never appear in list, detail, create, update, contract, or admin UI payloads.
- Read-only fields must be enforced at the API boundary, not only in the UI or registry metadata.
- Authorization checks must happen server-side.
- Superadmin-only routes must not accept impersonation or delegated tokens unless explicitly designed and tested.
- Audit failures must not silently corrupt or change the functional response path.
- Tenant boundaries must never rely only on client-provided values.
- Do not weaken authentication, authorization, schema filtering, or migration safety to make tests pass.

For auth, authorization, schema generation, migrations, tenant isolation, or data deletion: prefer one small regression test over broad untested changes.

---

## Core behavior

- Be brief by default.
- Prefer doing over explaining.
- Do not restate the task unless it is genuinely ambiguous.
- Do not produce long plans unless the task is complex, risky, cross-file, or has unclear tradeoffs.
- Do not dump large summaries of the repository or previous steps.
- Do not repeat information already visible in code, tests, or prior messages.
- Prefer exact answers over broad commentary.
- Do not announce tool calls or narrate routine investigation.
- Make the smallest correct change and stop.

---

## Output style

Normal responses should be one of:

- 3-8 short bullets, or
- 1-4 short paragraphs.

Avoid:

- motivational filler,
- long preambles,
- repeating the prompt,
- explaining obvious code,
- multiple alternatives unless asked,
- broad future work unless it is a real risk.

When the user asks for code changes, summarize after editing with one compact change note.

Preferred post-change structure:

```md
Changed:
- ...

Why:
- ...

Validation:
- ...

Risk / follow-up:
- ...
```

Omit sections that add no value. For a single-file low-risk edit, one sentence is enough.

---

## Planning rules

Only provide a plan if one of these is true:

- The task spans multiple files.
- The task is architecturally risky.
- The task has unclear tradeoffs.
- The task may affect security, migrations, or data safety.
- The task may require irreversible changes.

If a plan is needed:

- Use 3-5 bullets maximum.
- Keep it high-level.
- Do not include obvious actions.
- Stop planning once implementation can start.

---

## Editing rules

- Prefer minimal diffs over rewrites.
- Touch the fewest files necessary.
- Preserve existing structure unless there is a clear reason to refactor.
- Do not reformat unrelated code.
- Do not rename symbols unless required.
- Do not add abstraction for future flexibility unless explicitly requested.
- Prefer extending existing modules over creating new ones when reasonable.
- Keep helpers small and local unless reuse is clear.
- Avoid unnecessary comments.
- Add comments only when intent is non-obvious.
- Do not perform opportunistic cleanup unrelated to the task.
- After edits, show only the diff or a compact change note. Do not paste full files unless asked.

---

## Reading rules

- Read only the files needed for the task.
- Skim before deep reading.
- Inspect the relevant section first when a file is large.
- Do not echo file contents back to the user.
- Quote only decisive snippets.
- When investigating, collect the minimum evidence needed to act.
- If repository status conflicts with this file, trust the repository.

---

## Code generation rules

- Write production-usable code, not illustrative pseudo-code.
- Match existing project style and naming.
- Prefer straightforward code over generic frameworks.
- Avoid duplicate code when a small adaptation of existing code works.
- Keep public APIs stable unless the user requested a breaking change.
- Preserve backwards compatibility when reasonable.
- Make failure modes explicit where security or data integrity is involved.

---

## Testing rules

- Add or update only the tests needed to prove the change.
- Reuse existing fixtures and test patterns.
- Do not create broad test scaffolding unless required.
- For simple logic changes, prefer targeted tests.
- For risky changes, add one regression test that would have failed before.
- For auth, RBAC, tenant isolation, schema filtering, migrations, or data deletion, tests are expected unless there is a clear reason not to add them.
- Do not claim validation that was not run.

---

## Debugging rules

- State the likely cause in one sentence once enough evidence exists.
- Do not narrate every failed hypothesis.
- Do not paste large logs.
- Summarize command output instead of dumping it.
- Quote only the decisive error line or snippet.
- If a command fails because of missing local services or environment variables, say so directly and stop guessing.

---

## Migration and data-safety rules

For migrations, schema changes, destructive actions, or tenant-affecting changes:

- Verify current models and existing migration history first.
- Avoid destructive migrations unless explicitly requested.
- Preserve existing data unless the task explicitly says otherwise.
- Mention irreversible or risky operations before applying them.
- Add downgrade logic if the project convention expects it.
- Add focused migration or model tests where existing patterns allow.

---

## Documentation rules

- Do not create broad documentation updates unless asked.
- Keep docs close to the changed behavior.
- Prefer updating existing docs over creating new files.
- Do not document planned features as implemented.
- Mark experimental or future-facing behavior clearly.

---

## Decision defaults

When multiple valid options exist, prefer this order:

1. Smallest safe change.
2. Lowest token cost.
3. Consistency with the current codebase.
4. Testability.
5. Elegance.

---

## Repository-specific defaults

- Assume the user values incremental progress over large rewrites.
- Assume strict scope control.
- Assume tests should prove behavior, not architecture.
- Assume concise output is preferred unless explicitly requested otherwise.
- Assume implemented code has priority over older plans or generated summaries.
- Keep planned enterprise features outside the core until explicitly requested.
- Keep the built-in UI lightweight unless the task specifically targets UI expansion.

---

## Forbidden habits

- No long preambles.
- No echoing the prompt.
- No explaining obvious code.
- No announcing routine tool use.
- No broad repository summaries unless asked.
- No multiple alternative implementations unless requested.
- No speculative feature work.
- No opportunistic cleanup.
- No invented commands, files, routes, or architecture.
- No treating planned features as implemented.

---

## One-line operating principle

**Make the smallest correct change, explain it briefly, validate it honestly, and stop.**
