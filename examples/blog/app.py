import os

# SQLite für lokales Testen — muss vor allen coreAdmin-Imports gesetzt werden
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///blog.db")

from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import String, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

import coreAdmin_api.admin_config  # noqa: F401 — registriert User, Role, Tenant
from coreAdmin_api.admin import admin_site
from coreAdmin_api.admin.model_admin import ModelAdmin
from coreAdmin_api.admin.router import create_coreadmin
from coreAdmin_api.auth import hash_password
from coreAdmin_api.core.config import CoreAdminConfig
from coreAdmin_api.database import AsyncSessionLocal
from coreAdmin_api.models.base import Base, TimestampedBase
from coreAdmin_api.models.role import Role       # noqa: F401 — register table
from coreAdmin_api.models.tenant import Tenant   # noqa: F401 — register table
from coreAdmin_api.models.user import User
from coreAdmin_api.routers import auth, health
from coreAdmin_api.routers.admin_ui import router as admin_ui_router, get_static_app
from coreAdmin_api.settings import settings


# ---------------------------------------------------------------------------
# Datenmodell
# ---------------------------------------------------------------------------

class Post(TimestampedBase):
    __tablename__ = "posts"

    title:     Mapped[str]       = mapped_column(String(255), nullable=False)
    content:   Mapped[str]       = mapped_column(Text,        nullable=False, default="")
    author:    Mapped[str]       = mapped_column(String(255), nullable=False, default="")
    published: Mapped[bool]      = mapped_column(Boolean,     nullable=False, default=False)


# ---------------------------------------------------------------------------
# Admin-Konfiguration
# ---------------------------------------------------------------------------

class PostAdmin(ModelAdmin):
    model        = Post
    label        = "Post"
    label_plural = "Posts"
    list_display = ["title", "author", "published", "created_at"]
    search_fields  = ["title", "content", "author"]
    filter_fields  = ["published"]
    ordering       = ["-created_at"]
    readonly_fields = ["id", "created_at", "updated_at"]


admin_site.register(PostAdmin())


# ---------------------------------------------------------------------------
# App-Startup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Tabellen anlegen (ersetzt Alembic für dieses Beispiel)
    from coreAdmin_api.database import engine as db_engine
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Standard-Superadmin anlegen, falls keiner existiert
    async with AsyncSessionLocal() as session:
        from sqlalchemy import select
        existing = await session.execute(select(User).where(User.is_superadmin == True))
        if existing.scalars().first() is None:
            admin = User(
                email="admin@example.com",
                hashed_password=hash_password("admin123"),
                full_name="Admin",
                is_active=True,
                is_superadmin=True,
            )
            session.add(admin)
            await session.commit()
            print("\n✓ Superadmin angelegt: admin@example.com / admin123\n")

    yield


app = FastAPI(title="Blog Admin", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(health.router)

create_coreadmin(app, config=CoreAdminConfig())

app.mount(f"{settings.ADMIN_UI_PATH}/static", get_static_app(), name="admin-static")
app.include_router(admin_ui_router, prefix=settings.ADMIN_UI_PATH)
