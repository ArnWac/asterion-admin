import os

# SQLite für lokales Testen — muss vor allen coreAdmin-Imports gesetzt werden
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///blog.db")

from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import String, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

import adminfoundry.admin_config  # noqa: F401 — registriert User, Role, Tenant
from adminfoundry.admin import admin_site
from adminfoundry.admin.actions import AdminAction
from adminfoundry.admin.model_admin import ModelAdmin
from adminfoundry.admin.router import create_coreadmin
from adminfoundry.auth import hash_password
from adminfoundry.core.config import CoreAdminConfig
from adminfoundry.database import AsyncSessionLocal
from adminfoundry.models.base import Base, TimestampedBase
from adminfoundry.models.role import Role       # noqa: F401 — register table
from adminfoundry.models.tenant import Tenant   # noqa: F401 — register table
from adminfoundry.models.user import User
from adminfoundry.routers import auth, health


# ---------------------------------------------------------------------------
# Datenmodell
# ---------------------------------------------------------------------------

class Post(TimestampedBase):
    __tablename__ = "posts"

    title:     Mapped[str]       = mapped_column(String(255), nullable=False)
    content:   Mapped[str]       = mapped_column(Text,        nullable=False, default="")
    author:    Mapped[str]       = mapped_column(String(255), nullable=False, default="")
    published: Mapped[bool]      = mapped_column(Boolean,     nullable=False, default=False)


def _word_count(obj: Post) -> int:
    return len((obj.content or "").split())


def _read_time(obj: Post) -> str:
    minutes = max(1, round(_word_count(obj) / 200))
    return f"{minutes} min"


def _excerpt(obj: Post) -> str:
    body = (obj.content or "").strip()
    return body[:100] + ("…" if len(body) > 100 else "")


# ---------------------------------------------------------------------------
# Admin-Konfiguration
# ---------------------------------------------------------------------------

class PublishAction(AdminAction):
    name    = "publish_all"
    label   = "Publish selected"
    confirm = True
    danger  = False

    async def execute(self, objects, db, user):
        for obj in objects:
            obj.published = True
        await db.commit()
        return {"summary": f"{len(objects)} post(s) published"}


class PostAdmin(ModelAdmin):
    model        = Post
    label        = "Post"
    label_plural = "Posts"
    list_display    = ["title", "author", "word_count", "read_time", "published", "created_at"]
    search_fields   = ["title", "content", "author"]
    filter_fields   = ["published"]
    ordering        = ["-created_at"]
    readonly_fields = ["id", "created_at", "updated_at"]
    actions         = [PublishAction()]
    fieldsets = [
        ("Content",     ["title", "content"]),
        ("Publishing",  ["author", "published"]),
    ]
    computed_fields = {
        "word_count": _word_count,
        "read_time":  _read_time,
        "excerpt":    _excerpt,
    }


admin_site.register(PostAdmin())


# ---------------------------------------------------------------------------
# App-Startup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Tabellen anlegen (ersetzt Alembic für dieses Beispiel)
    from adminfoundry.database import engine as db_engine
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
            print("\nSuperadmin angelegt: admin@example.com / admin123\n")

    yield


app = FastAPI(title="Blog Admin", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(health.router)

create_coreadmin(app, config=CoreAdminConfig(
    default_date_format="eu",
    default_show_timezone=True,
))
