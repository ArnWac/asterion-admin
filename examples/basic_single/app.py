"""
Single-tenant blog example.

Run:
    uvicorn examples.basic_single.app:app --reload

Admin UI: http://127.0.0.1:8000/admin-ui
"""
import examples.basic_single.database  # noqa: F401 — set DATABASE_URL before any other import

from contextlib import asynccontextmanager

from fastapi import FastAPI

import examples.basic_single.admin_config  # noqa: F401 — register admins
from adminfoundry import create_admin, CoreAdminConfig
from adminfoundry.settings import settings
from examples.basic_single.seed import seed, print_banner


@asynccontextmanager
async def lifespan(app: FastAPI):
    await seed()
    print_banner()
    yield


app = create_admin(
    config=CoreAdminConfig.from_settings(settings),
    title="adminfoundry — basic_single",
    lifespan=lifespan,
)
