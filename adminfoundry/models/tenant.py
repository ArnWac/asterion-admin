from __future__ import annotations
from typing import TYPE_CHECKING
from sqlalchemy import String, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from adminfoundry.models.base import TimestampedBase

if TYPE_CHECKING:
    from adminfoundry.models.tenant_membership import TenantMembership


class Tenant(TimestampedBase):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(63), unique=True, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Locale — serve as the middle tier between app defaults and user preferences.
    # All nullable: None means "inherit from app default".
    # timezone: IANA name, e.g. "Europe/Berlin", "America/New_York", "UTC"
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # language: BCP 47 tag, e.g. "de", "en", "fr", "pt-BR"
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # date_format: "locale" | "iso" | "eu" | "us" | "custom"
    date_format: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # date_pattern: strftime pattern used when date_format = "custom"
    date_pattern: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # IP allowlist: JSON array of CIDR strings, e.g. ["10.0.0.0/8", "203.0.113.5/32"].
    # Null means unrestricted. Enforced in TenantMiddleware.
    allowed_cidrs: Mapped[str | None] = mapped_column(Text, nullable=True)

    memberships: Mapped[list[TenantMembership]] = relationship(
        "TenantMembership", back_populates="tenant", cascade="all, delete-orphan"
    )

    @property
    def schema_name(self) -> str:
        return f"tenant_{self.slug}"
