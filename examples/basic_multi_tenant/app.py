"""
Multi-tenant quickstart example using subdomain resolution.

Run:
    uvicorn examples.basic_multi_tenant.app:app --reload --host 0.0.0.0

- Superadmin panel: http://localhost:8000/admin-ui
- Tenant alpha:     http://alpha.localhost:8000/admin-ui
- Tenant beta:      http://beta.localhost:8000/admin-ui
"""
import examples.basic_multi_tenant.database  # noqa: F401 — set env vars before imports

from contextlib import asynccontextmanager

from fastapi import FastAPI

import examples.basic_multi_tenant.admin_config  # noqa: F401 — register admins
from adminfoundry.admin.router import create_admin
from adminfoundry.auth import hash_password
from adminfoundry.core.config import CoreAdminConfig
from adminfoundry.database import AsyncSessionLocal
from adminfoundry.middleware.tenant import TenantMiddleware
from adminfoundry.models.base import Base
from adminfoundry.models.role import Role  # noqa: F401 — register table
from adminfoundry.models.tenant import Tenant
from adminfoundry.models.user import User
from adminfoundry.routers import auth, health, tenants


@asynccontextmanager
async def lifespan(app: FastAPI):
    from adminfoundry.database import engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        from sqlalchemy import select

        if not (await session.execute(select(User).where(User.is_superadmin == True))).scalars().first():
            session.add(User(
                email="admin@example.com",
                hashed_password=hash_password("admin123"),
                full_name="Super Admin",
                is_active=True,
                is_superadmin=True,
            ))
            print("\nSuperadmin: admin@example.com / admin123")

        for slug, name in [("alpha", "Alpha Corp"), ("beta", "Beta Ltd")]:
            if not (await session.execute(select(Tenant).where(Tenant.slug == slug))).scalars().first():
                session.add(Tenant(name=name, slug=slug, is_active=True))
                print(f"Tenant: {slug}")

        await session.commit()
    yield


app = FastAPI(title="Basic Multi-Tenant Admin", lifespan=lifespan)
app.add_middleware(TenantMiddleware)
app.include_router(auth.router)
app.include_router(health.router)
app.include_router(tenants.router)

create_admin(app, config=CoreAdminConfig(enable_multi_tenant=True))
