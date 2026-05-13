"""Seed the multi-tenant demo: 1 superadmin + 2 tenants + 1 tenant admin each.

Idempotent. Re-running does not overwrite existing rows.
"""
import asyncio

import examples.basic_multi.database  # noqa: F401 — set env vars

from sqlalchemy import select

from adminfoundry.auth import hash_password
from adminfoundry.database import AsyncSessionLocal, engine
from adminfoundry.models import Role, Tenant, User, user_roles
from adminfoundry.models.base import Base

# Trigger admin registrations so all model tables are imported.
import examples.basic_multi.admin_config  # noqa: F401


SUPERADMIN_EMAIL = "admin@example.com"
SUPERADMIN_PASSWORD = "admin123"  # demo only

TENANTS = [
    ("acme",  "Acme Corp",  "Europe/Berlin"),
    ("orbit", "Orbit Ltd",  "America/New_York"),
]
TENANT_ADMIN_PASSWORD = "admin123"  # demo only


async def seed() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        # Superadmin
        existing = (await session.execute(
            select(User).where(User.email == SUPERADMIN_EMAIL)
        )).scalars().first()
        if existing is None:
            session.add(User(
                email=SUPERADMIN_EMAIL,
                hashed_password=hash_password(SUPERADMIN_PASSWORD),
                full_name="Super Admin",
                is_active=True,
                is_superadmin=True,
            ))
            await session.flush()

        # Tenants + tenant_admin role + tenant_admin user
        for slug, name, tz in TENANTS:
            tenant = (await session.execute(
                select(Tenant).where(Tenant.slug == slug)
            )).scalars().first()
            if tenant is None:
                tenant = Tenant(name=name, slug=slug, is_active=True, timezone=tz)
                session.add(tenant)
                await session.flush()

            role = (await session.execute(
                select(Role).where(Role.name == "tenant_admin", Role.tenant_id == tenant.id)
            )).scalars().first()
            if role is None:
                role = Role(name="tenant_admin", description="Tenant admin", tenant_id=tenant.id)
                session.add(role)
                await session.flush()

            email = f"admin@{slug}.test"
            user = (await session.execute(
                select(User).where(User.email == email)
            )).scalars().first()
            if user is None:
                user = User(
                    email=email,
                    hashed_password=hash_password(TENANT_ADMIN_PASSWORD),
                    full_name=f"{name} Admin",
                    is_active=True,
                    is_superadmin=False,
                )
                session.add(user)
                await session.flush()
                await session.execute(
                    user_roles.insert().values(user_id=user.id, role_id=role.id)
                )

        await session.commit()


def print_banner() -> None:
    tenant_lines = []
    for slug, _name, _tz in TENANTS:
        tenant_lines.append(f"""  {slug}
    X-Tenant-Slug: {slug}
    tenant admin: admin@{slug}.test
    password:     {TENANT_ADMIN_PASSWORD}
""")
    print(f"""
adminfoundry demo ready

Admin UI:
  http://127.0.0.1:8000/admin-ui

Global superadmin (demo only):
  email:    {SUPERADMIN_EMAIL}
  password: {SUPERADMIN_PASSWORD}

Tenants:
{''.join(tenant_lines)}""")


if __name__ == "__main__":
    asyncio.run(seed())
    print_banner()
