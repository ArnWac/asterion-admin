from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from asterion.models.base import GlobalModel
from asterion.security.validation import validate_schema_name

if TYPE_CHECKING:
    from asterion.models.tenant_membership import TenantMembership


class Tenant(GlobalModel):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    slug: Mapped[str] = mapped_column(
        String(63),
        unique=True,
        nullable=False,
        index=True,
    )

    schema_name: Mapped[str] = mapped_column(
        String(63),
        unique=True,
        nullable=False,
        index=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    #: Set when the tenant is offboarded (roadmap G6). ``None`` means the tenant
    #: is live. In ``archive`` mode the row survives as a tombstone with this set
    #: + ``is_active=False`` (slug stays reserved); ``drop`` mode deletes the row.
    offboarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    date_format: Mapped[str | None] = mapped_column(String(16), nullable=True)
    date_pattern: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Keep only if you still want tenant IP allowlist in middleware.
    # Otherwise remove for now.
    allowed_cidrs: Mapped[str | None] = mapped_column(Text, nullable=True)

    memberships: Mapped[list[TenantMembership]] = relationship(
        "TenantMembership",
        back_populates="tenant",
        cascade="all, delete-orphan",
    )

    @validates("schema_name")
    def _validate_schema_name(self, key: str, value: str) -> str:
        return validate_schema_name(value)
