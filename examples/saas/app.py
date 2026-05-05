"""
SaaS Multi-Tenant Beispiel — Subdomain-Routing

Eine App-Instanz, zwei Admin-Kontexte:

  admin.yourdomain.com        → kein Tenant  → Superadmin-Panel
                                               (Users, Roles, Tenants verwalten)

  acme.yourdomain.com         → Tenant acme  → nur acme-Projects sichtbar
  globex.yourdomain.com       → Tenant globex → nur globex-Projects sichtbar

Lokales Testen ohne echte Subdomains:
  X-Tenant-Slug: acme   Header setzen  → Tenant-Panel
  kein Header           →              → Superadmin-Panel

Starten:
  uvicorn examples.saas.app:app --reload

Nginx-Beispiel (Production):
  server {
      server_name ~^(?<slug>.+)\\.yourdomain\\.com$;
      location / {
          proxy_pass http://127.0.0.1:8000;
          proxy_set_header X-Tenant-Slug $slug;
      }
  }
  server {
      server_name admin.yourdomain.com;
      location / {
          proxy_pass http://127.0.0.1:8000;
          # kein X-Tenant-Slug Header → Superadmin-Kontext
      }
  }
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///saas.db")
os.environ.setdefault("MULTI_TENANT", "true")
os.environ.setdefault("TENANT_RESOLUTION_STRATEGY", "header")  # "subdomain" in Production

from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import String, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

import adminfoundry.admin_config  # noqa: F401 — registriert User, Role, Tenant
from adminfoundry.admin import admin_site
from adminfoundry.admin.model_admin import ModelAdmin
from adminfoundry.admin.router import create_coreadmin
from adminfoundry.auth import hash_password
from adminfoundry.core.config import CoreAdminConfig
from adminfoundry.database import AsyncSessionLocal
from adminfoundry.middleware.tenant import TenantMiddleware
from adminfoundry.models.base import Base, TimestampedBase
from adminfoundry.models.role import Role      # noqa: F401
from adminfoundry.models.tenant import Tenant
from adminfoundry.models.user import User
from adminfoundry.routers import auth, health, tenants
from adminfoundry.routers.admin_ui import router as admin_ui_router, get_static_app
from adminfoundry.settings import settings


# ---------------------------------------------------------------------------
# App-Datenmodell — tenant-scoped
# Jeder Tenant sieht nur seine eigenen Projects.
# ---------------------------------------------------------------------------

class Project(TimestampedBase):
    __tablename__ = "projects"

    name:      Mapped[str]  = mapped_column(String(255), nullable=False)
    active:    Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    tenant_id: Mapped[str]  = mapped_column(
        String(36), ForeignKey("tenants.id"), nullable=True, index=True
    )


# ---------------------------------------------------------------------------
# Admin-Registrierungen
#
# ProjectAdmin  — tenant_scoped=True
#   → nur sichtbar wenn ein Tenant-Kontext gesetzt ist (Tenant-Panel)
#   → Superadmin ohne Tenant-Kontext sieht es nicht (bewusst)
#
# UserAdmin / RoleAdmin / TenantAdmin kommen aus adminfoundry.admin_config
#   → admin_only=True, kein tenant_scoped → nur im Superadmin-Panel
# ---------------------------------------------------------------------------

class ProjectAdmin(ModelAdmin):
    model         = Project
    label         = "Project"
    label_plural  = "Projects"
    description   = "Projekte des aktiven Tenants"
    list_display  = ["name", "active", "created_at"]
    search_fields = ["name"]
    filter_fields = ["active"]
    ordering      = ["name"]
    readonly_fields = ["id", "created_at", "updated_at", "tenant_id"]
    tenant_scoped = True


admin_site.register(ProjectAdmin())


# ---------------------------------------------------------------------------
# Startup — Demo-Daten
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    from adminfoundry.database import engine as db_engine
    from sqlalchemy import select

    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        # Superadmin
        if not (await session.execute(
            select(User).where(User.is_superadmin == True)
        )).scalars().first():
            session.add(User(
                email="admin@example.com",
                hashed_password=hash_password("admin123"),
                full_name="Super Admin",
                is_active=True,
                is_superadmin=True,
            ))
            print("\n✓ Superadmin: admin@example.com / admin123")

        # Demo-Tenants
        for slug, name in [("acme", "Acme Corp"), ("globex", "Globex Inc")]:
            if not (await session.execute(
                select(Tenant).where(Tenant.slug == slug)
            )).scalars().first():
                session.add(Tenant(name=name, slug=slug, is_active=True))
                print(f"✓ Tenant: {slug}")

        await session.commit()

    print("""
─────────────────────────────────────────────
  Superadmin-Panel   →  kein Header
  Tenant acme        →  X-Tenant-Slug: acme
  Tenant globex      →  X-Tenant-Slug: globex
  Admin UI           →  http://localhost:8000/admin-ui
─────────────────────────────────────────────
""")
    yield


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="SaaS Admin — Multi-Tenant", lifespan=lifespan)

app.add_middleware(TenantMiddleware)
app.include_router(auth.router)
app.include_router(health.router)
app.include_router(tenants.router)

create_coreadmin(app, config=CoreAdminConfig(enable_multi_tenant=True))

app.mount(f"{settings.ADMIN_UI_PATH}/static", get_static_app(), name="admin-static")
app.include_router(admin_ui_router, prefix=settings.ADMIN_UI_PATH)
