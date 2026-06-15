"""Single-tenant demo models.

``Post`` is intentionally feature-rich: it carries enough columns to let
``admin_config.py`` showcase the *entire* ``ModelAdmin`` surface (badges,
conditional/dependent fields, fieldsets/tabs, widgets, inline edit, a
protected field, a per-field policy, calculated fields, an inline child).
``Comment`` is the inline child edited inside the Post form.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from adminfoundry.models.base import GUID, GlobalModel


class PostStatus(enum.StrEnum):
    draft = "draft"
    review = "review"
    published = "published"
    archived = "archived"


class PostCategory(enum.StrEnum):
    engineering = "engineering"
    product = "product"


class PostSubcategory(enum.StrEnum):
    # engineering
    backend = "backend"
    frontend = "frontend"
    infra = "infra"
    # product
    roadmap = "roadmap"
    research = "research"


class Post(GlobalModel):
    __tablename__ = "posts"

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str | None] = mapped_column(String(280), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    author: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    status: Mapped[PostStatus] = mapped_column(
        SAEnum(PostStatus, name="post_status"),
        nullable=False,
        default=PostStatus.draft,
    )
    category: Mapped[PostCategory] = mapped_column(
        SAEnum(PostCategory, name="post_category"),
        nullable=False,
        default=PostCategory.engineering,
    )
    # Narrowed by ``category`` via ``field_dependencies`` in admin_config.
    subcategory: Mapped[PostSubcategory | None] = mapped_column(
        SAEnum(PostSubcategory, name="post_subcategory"),
        nullable=True,
    )

    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Shown in the form only when status == "published" (field_conditions).
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Read-only for non-superadmins via a per-field AdminPolicy.
    internal_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Never serialized / accepted — listed in protected_fields.
    api_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)

    comments: Mapped[list[Comment]] = relationship(
        "Comment",
        back_populates="post",
        cascade="all, delete-orphan",
    )


class Comment(GlobalModel):
    __tablename__ = "post_comments"

    post_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    post: Mapped[Post] = relationship("Post", back_populates="comments")
