"""Seed the single-tenant demo: one superadmin. Idempotent."""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import select

# Ensure the app-local Post table is registered in GlobalModel.metadata.
import examples.basic_single.models  # noqa: F401
from asterion.auth.password import hash_password
from asterion.db.session import DatabaseManager
from asterion.models import User
from asterion.models.base import GlobalModel

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "admin123"  # demo only


async def seed(db: DatabaseManager) -> None:
    async with db.engine.begin() as conn:
        await conn.run_sync(GlobalModel.metadata.create_all)

    async with db.session() as session:
        existing = (
            (await session.execute(select(User).where(User.email == ADMIN_EMAIL))).scalars().first()
        )
        if existing is None:
            session.add(
                User(
                    email=ADMIN_EMAIL,
                    hashed_password=hash_password(ADMIN_PASSWORD),
                    full_name="Admin",
                    is_active=True,
                    is_superadmin=True,
                )
            )
            await session.commit()


def print_banner() -> None:
    print(f"""
asterion demo ready

Admin UI:
  http://127.0.0.1:8000/admin

Global superadmin (demo only — do not use in production):
  email:    {ADMIN_EMAIL}
  password: {ADMIN_PASSWORD}
""")


async def _run_standalone() -> None:
    url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./basic_single.db")
    db = DatabaseManager(url)
    try:
        await seed(db)
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(_run_standalone())
    print_banner()
