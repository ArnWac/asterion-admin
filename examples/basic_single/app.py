"""
Single-tenant blog example.

Run:
    uvicorn examples.basic_single.app:app --reload

Admin UI: http://127.0.0.1:8000/admin
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from adminfoundry import CoreAdminConfig, create_admin
from examples.basic_single.admin_config import register
from examples.basic_single.seed import print_banner, seed

config = CoreAdminConfig(
    database_url=os.environ.get(
        "DATABASE_URL",
        "sqlite+aiosqlite:///./basic_single.db",
    ),
    secret_key=os.environ.get("SECRET_KEY", "dev-secret"),
    app_title="adminfoundry — basic_single",
    enable_multi_tenant=False,
    enable_builtin_admins=False,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await seed(app.state.adminfoundry.db)
    print_banner()
    yield


app = create_admin(
    config=config,
    register=register,
    lifespan=lifespan,
)
