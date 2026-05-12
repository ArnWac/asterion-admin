"""
SaaS Multi-Tenant Beispiel — Subdomain-Routing

Eine App-Instanz, zwei Admin-Kontexte:

  localhost:8000          → kein Tenant  → Superadmin-Panel
  acme.localhost:8000     → Tenant acme  → nur acme-Projects sichtbar
  globex.localhost:8000   → Tenant globex → nur globex-Projects sichtbar

*.localhost wird von modernen Betriebssystemen automatisch auf 127.0.0.1 aufgeloest.
Kein /etc/hosts-Eintrag noetig.

Starten:
  uvicorn examples.saas.app:app --reload --host 0.0.0.0

Nginx-Beispiel (Production):
  server {
      server_name ~^(?<slug>.+)\\.yourdomain\\.com$;
      location / { proxy_pass http://127.0.0.1:8000; }
  }
  server {
      server_name yourdomain.com;
      location / { proxy_pass http://127.0.0.1:8000; }
  }
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///saas.db")
os.environ.setdefault("MULTI_TENANT", "true")
os.environ.setdefault("TENANT_RESOLUTION_STRATEGY", "subdomain")

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
from adminfoundry.models.role import Role, user_roles
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
            print("\nSuperadmin: admin@example.com / admin123")

        # Demo-Tenants + je ein tenant_admin-User
        tenant_configs = [
            ("acme",   "Acme Corp",  "Europe/Berlin",   "de", "eu",     None),
            ("globex", "Globex Inc", "America/New_York", "en", "us",     None),
        ]
        for slug, name, tz, lang, dfmt, dpat in tenant_configs:
            tenant = (await session.execute(
                select(Tenant).where(Tenant.slug == slug)
            )).scalars().first()
            if not tenant:
                tenant = Tenant(
                    name=name, slug=slug, is_active=True,
                    timezone=tz, language=lang, date_format=dfmt, date_pattern=dpat,
                )
                session.add(tenant)
                await session.flush()
                print(f"Tenant angelegt: {slug} ({tz}, {lang}, {dfmt})")

            # Role "tenant_admin" für diesen Tenant
            role = (await session.execute(
                select(Role).where(Role.name == "tenant_admin", Role.tenant_id == tenant.id)
            )).scalars().first()
            if not role:
                role = Role(name="tenant_admin", description="Tenant admin", tenant_id=tenant.id)
                session.add(role)
                await session.flush()

            # Demo-User für diesen Tenant
            user_email = f"{slug}-admin@example.com"
            demo_user = (await session.execute(
                select(User).where(User.email == user_email)
            )).scalars().first()
            if not demo_user:
                demo_user = User(
                    email=user_email,
                    hashed_password=hash_password("tenant123"),
                    full_name=f"{name} Admin",
                    is_active=True,
                    is_superadmin=False,
                )
                session.add(demo_user)
                await session.flush()
                await session.execute(
                    user_roles.insert().values(user_id=demo_user.id, role_id=role.id)
                )
                print(f"Tenant-User angelegt: {user_email}")

        await session.commit()

    print("""
-------------------------------------------------
  Superadmin-Panel   ->  http://localhost:8000/admin-ui
  Tenant acme        ->  http://acme.localhost:8000/admin-ui
  Tenant globex      ->  http://globex.localhost:8000/admin-ui

  Superadmin:  admin@example.com    / admin123
  Acme-Admin:  acme-admin@example.com   / tenant123
  Globex-Admin: globex-admin@example.com / tenant123
-------------------------------------------------
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
