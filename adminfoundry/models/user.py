from __future__ import annotations
import uuid
from typing import TYPE_CHECKING
from sqlalchemy import String, Boolean, ForeignKey, Integer, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from adminfoundry.models.associations import user_roles
from adminfoundry.models.base import TimestampedBase, GUID

if TYPE_CHECKING:
    from adminfoundry.models.role import Role


class User(TimestampedBase):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # TOTP / 2FA
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    totp_backup_codes: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Incremented on privilege change to invalidate all outstanding access tokens
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    roles: Mapped[list[Role]] = relationship(
        "Role", secondary=user_roles, back_populates="users", lazy="selectin"
    )
