"""Seed two demo tenants ("acme" and "globex") with one owner each.

Idempotent: re-running the script — or restarting the server — does not
duplicate rows. Requires PostgreSQL; schema-per-tenant isolation cannot
be expressed on SQLite.

The flow per tenant is:

  1. ``create_tenant_record`` writes the public ``tenants`` row.
  2. The owner user is created if missing.
  3. ``assign_owner_membership`` writes the public ``tenant_memberships`` row.
  4. ``bootstrap_tenant`` provisions the schema, runs the framework's
     tenant Alembic migrations, syncs the permission catalog, seeds the
     three default tenant roles (owner / admin / viewer), and assigns
     the owner membership to the owner role.
  5. We then ``CREATE TABLE`` the demo-specific Project + Ticket tables
     inside the freshly provisioned tenant schema and insert sample rows.
"""

from __future__ import annotations

import asyncio
import os
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion.auth.password import hash_password
from asterion.db.session import DatabaseManager
from asterion.models import User
from asterion.models.base import GlobalModel, TenantBase
from asterion.registry import AdminRegistry
from asterion.tenancy.bootstrap import (
    assign_owner_membership,
    bootstrap_tenant,
    create_tenant_record,
)
from asterion.tenancy.schema_names import make_tenant_schema_name
from examples.multi_tenant.admin_config import register as register_admins

# Ensure the example tenant models are registered in TenantBase.metadata
# before we create_all() inside each tenant schema.
from examples.multi_tenant.models import (
    Project,
    Ticket,
    TicketPriority,
    TicketStatus,
)

SUPERADMIN_EMAIL = "superadmin@example.com"
SUPERADMIN_PASSWORD = "superadmin123"  # demo only

TENANT_SEEDS: tuple[dict, ...] = (
    {
        "slug": "acme",
        "name": "Acme Corp",
        "owner_email": "owner@acme.test",
        "owner_password": "owner123",
        "owner_name": "Alice Acme",
        "projects": [
            {
                "key": "WEB",
                "name": "Acme Website",
                "description": "Public marketing site.",
                "tickets": [
                    ("Update hero image", TicketStatus.in_progress, TicketPriority.normal),
                    ("Fix mobile nav", TicketStatus.open, TicketPriority.high),
                ],
            },
            {
                "key": "API",
                "name": "Acme API",
                "description": "Customer-facing REST API.",
                "tickets": [
                    ("Add /v2/orders endpoint", TicketStatus.open, TicketPriority.urgent),
                ],
            },
        ],
    },
    {
        "slug": "globex",
        "name": "Globex Industries",
        "owner_email": "owner@globex.test",
        "owner_password": "owner123",
        "owner_name": "Greta Globex",
        "projects": [
            {
                "key": "OPS",
                "name": "Ops Tooling",
                "description": "Internal operations dashboards.",
                "tickets": [
                    ("Migrate dashboards to Grafana 11", TicketStatus.open, TicketPriority.low),
                    ("On-call rotation alert noise", TicketStatus.closed, TicketPriority.normal),
                ],
            },
        ],
    },
)


def _require_postgres(database_url: str) -> None:
    if "postgresql" in database_url:
        return
    raise RuntimeError(
        "examples.multi_tenant requires PostgreSQL — schema-per-tenant "
        "isolation cannot be expressed on SQLite. Set DATABASE_URL to a "
        "postgresql+asyncpg:// URL and re-run.\n"
        "  e.g. DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/asterion"
    )


async def _ensure_superadmin(db: DatabaseManager) -> None:
    async with db.session() as session, session.begin():
        existing = (
            await session.execute(select(User).where(User.email == SUPERADMIN_EMAIL))
        ).scalar_one_or_none()
        if existing is not None:
            return
        session.add(
            User(
                email=SUPERADMIN_EMAIL,
                hashed_password=hash_password(SUPERADMIN_PASSWORD),
                full_name="Demo Superadmin",
                is_active=True,
                is_superadmin=True,
            )
        )


async def _ensure_owner(db: DatabaseManager, email: str, password: str, name: str) -> uuid.UUID:
    async with db.session() as session, session.begin():
        existing = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if existing is not None:
            return existing.id
        owner = User(
            email=email,
            hashed_password=hash_password(password),
            full_name=name,
            is_active=True,
            is_superadmin=False,
        )
        session.add(owner)
        await session.flush()
        return owner.id


async def _seed_tenant_data(db: DatabaseManager, schema_name: str, projects: list[dict]) -> None:
    """Create the demo Project/Ticket tables inside ``schema_name`` and
    insert the sample rows. Idempotent on the (key, title) tuples."""
    # 1. Create the demo tables inside the tenant schema.
    async with db.engine.begin() as conn:
        await conn.execute(text(f'SET LOCAL search_path TO "{schema_name}", public'))
        await conn.run_sync(
            TenantBase.metadata.create_all,
            tables=[Project.__table__, Ticket.__table__],
        )

    # 2. Insert sample rows in a tenant-scoped session.
    factory = async_sessionmaker(db.engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await session.execute(text(f'SET LOCAL search_path TO "{schema_name}", public'))

        for proj_spec in projects:
            existing = (
                await session.execute(select(Project).where(Project.key == proj_spec["key"]))
            ).scalar_one_or_none()
            if existing is None:
                project = Project(
                    key=proj_spec["key"],
                    name=proj_spec["name"],
                    description=proj_spec.get("description"),
                )
                session.add(project)
                await session.flush()
            else:
                project = existing

            existing_titles = {
                row
                for row in (
                    await session.execute(
                        select(Ticket.title).where(Ticket.project_id == project.id)
                    )
                )
                .scalars()
                .all()
            }
            for title, status, priority in proj_spec["tickets"]:
                if title in existing_titles:
                    continue
                session.add(
                    Ticket(
                        project_id=project.id,
                        title=title,
                        status=status,
                        priority=priority,
                    )
                )


async def seed(db: DatabaseManager, database_url: str) -> None:
    _require_postgres(database_url)

    # Public-schema tables. In a real app these come from Alembic — for the
    # demo we accept the convenience of create_all so a fresh database is
    # ready in one command.
    async with db.engine.begin() as conn:
        await conn.run_sync(GlobalModel.metadata.create_all)

    await _ensure_superadmin(db)

    # The registry is used by bootstrap_tenant to sync PermissionCatalog so
    # that admin/viewer roles get the per-resource permission keys.
    registry = AdminRegistry()
    register_admins(registry)

    for spec in TENANT_SEEDS:
        slug = spec["slug"]
        owner_id = await _ensure_owner(
            db,
            email=spec["owner_email"],
            password=spec["owner_password"],
            name=spec["owner_name"],
        )

        async with db.session() as session, session.begin():
            tenant = await create_tenant_record(session, name=spec["name"], slug=slug)
            owner = (await session.execute(select(User).where(User.id == owner_id))).scalar_one()
            membership = await assign_owner_membership(session, tenant=tenant, user=owner)
            membership_id = membership.id

        async with db.session() as session:
            await bootstrap_tenant(
                slug,
                session,
                owner_membership_id=membership_id,
                database_url=database_url,
                registry=registry,
            )

        await _seed_tenant_data(db, make_tenant_schema_name(slug), spec["projects"])


def print_banner() -> None:
    lines = [
        "",
        "asterion multi-tenant demo ready",
        "",
        "Admin UI:  http://127.0.0.1:8000/admin",
        "",
        "Sign in as the global superadmin to see every tenant:",
        f"  email:    {SUPERADMIN_EMAIL}",
        f"  password: {SUPERADMIN_PASSWORD}",
        "",
        "Or sign in as a tenant owner and send the X-Tenant-Slug header to scope",
        "API requests to that tenant's schema:",
    ]
    for spec in TENANT_SEEDS:
        lines.append(
            f"  {spec['slug']:<8}  {spec['owner_email']:<22}  password: {spec['owner_password']}"
        )
    lines.append("")
    lines.append("Example API call:")
    lines.append("  curl -H 'X-Tenant-Slug: acme' \\")
    lines.append("       -H 'Authorization: Bearer <token>' \\")
    lines.append("       http://127.0.0.1:8000/api/v1/admin/projects")
    lines.append("")
    print("\n".join(lines))


async def _run_standalone() -> None:
    url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/asterion",
    )
    db = DatabaseManager(url)
    try:
        await seed(db, url)
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(_run_standalone())
    print_banner()
