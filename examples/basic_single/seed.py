"""Seed the single-tenant demo: one superadmin. Idempotent."""
import asyncio

import examples.basic_single.database  # noqa: F401 — set DATABASE_URL

from sqlalchemy import select

from adminfoundry.auth import hash_password
from adminfoundry.database import AsyncSessionLocal, engine
from adminfoundry.models import User
from adminfoundry.models.base import Base

# Trigger admin registrations so all model tables are imported.
import examples.basic_single.admin_config  # noqa: F401


ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "admin123"  # demo only


async def seed() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        existing = (await session.execute(
            select(User).where(User.email == ADMIN_EMAIL)
        )).scalars().first()
        if existing is None:
            session.add(User(
                email=ADMIN_EMAIL,
                hashed_password=hash_password(ADMIN_PASSWORD),
                full_name="Admin",
                is_active=True,
                is_superadmin=True,
            ))
            await session.commit()


def print_banner() -> None:
    print("""
adminfoundry demo ready

Admin UI:
  http://127.0.0.1:8000/admin-ui

Global superadmin (demo only — do not use in production):
  email:    {email}
  password: {password}
""".format(email=ADMIN_EMAIL, password=ADMIN_PASSWORD))


if __name__ == "__main__":
    asyncio.run(seed())
    print_banner()
