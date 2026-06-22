"""
Multi-tenant issue-tracker example.

Run (requires PostgreSQL — see README):

    DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/asterion \\
        SECRET_KEY=$(openssl rand -hex 32) \\
        uvicorn examples.multi_tenant.app:app --reload

Admin UI: http://127.0.0.1:8000/admin
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from asterion import CoreAdminConfig, create_admin
from asterion.extensions.import_export import ImportExportExtension
from examples.multi_tenant.admin_config import register
from examples.multi_tenant.seed import print_banner, seed

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/asterion",
)

# How the active tenant is resolved per request. Switch with the env var:
#   TENANT_RESOLUTION=header     -> read the X-Tenant-Slug header (default)
#   TENANT_RESOLUTION=subdomain  -> read the first Host label, e.g.
#                                   acme.localhost:8000 -> tenant "acme"
# With "subdomain", reach a tenant at http://<slug>.localhost:8000 and the
# global superadmin at http://localhost:8000 (a single-label host has no
# subdomain, so it resolves to the public schema).
TENANT_RESOLUTION = os.environ.get("TENANT_RESOLUTION", "header")

config = CoreAdminConfig(
    database_url=DATABASE_URL,
    secret_key=os.environ.get("SECRET_KEY", "dev-secret-change-me-in-production"),
    app_title="asterion — multi-tenant demo",
    enable_multi_tenant=True,
    # All admins (including the tenant RBAC ones) are registered
    # explicitly in admin_config.py — nothing is auto-installed.
    enable_builtin_admins=False,
    tenant_resolution=TENANT_RESOLUTION,
    tenant_header_name="X-Tenant-Slug",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await seed(app.state.asterion.db, DATABASE_URL)
    print_banner()
    yield


app = create_admin(
    config=config,
    register=register,
    extensions=[ImportExportExtension()],
    lifespan=lifespan,
)
