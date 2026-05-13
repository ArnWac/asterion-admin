"""
Single-tenant quickstart example.

Run:
    uvicorn examples.basic_single_tenant.app:app --reload

Admin UI: http://localhost:8000/admin-ui
"""
import examples.basic_single_tenant.database  # noqa: F401 — set DATABASE_URL before any other import

from contextlib import asynccontextmanager

from fastapi import FastAPI

import examples.basic_single_tenant.admin_config  # noqa: F401 — register Note admin
from adminfoundry.admin.router import create_admin
from adminfoundry.auth import hash_password
from adminfoundry.core.config import CoreAdminConfig
from adminfoundry.database import AsyncSessionLocal
from adminfoundry.models.base import Base
from adminfoundry.models.role import Role  # noqa: F401 — register table
from adminfoundry.models.user import User
from adminfoundry.routers import auth, health


@asynccontextmanager
async def lifespan(app: FastAPI):
    from adminfoundry.database import engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        from sqlalchemy import select
        existing = await session.execute(select(User).where(User.is_superadmin == True))
        if existing.scalars().first() is None:
            session.add(User(
                email="admin@example.com",
                hashed_password=hash_password("admin123"),
                full_name="Admin",
                is_active=True,
                is_superadmin=True,
            ))
            await session.commit()
            print("\nSuperadmin: admin@example.com / admin123\n")
    yield


app = FastAPI(title="Basic Single-Tenant Admin", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(health.router)

create_admin(app, config=CoreAdminConfig())
