from __future__ import annotations
import uuid
from typing import TYPE_CHECKING
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from adminfoundry.models.associations import user_roles
from adminfoundry.models.base import GUID, TimestampedBase

if TYPE_CHECKING:
    from adminfoundry.models.user import User


__all__ = ["Role", "user_roles"]


class Role(TimestampedBase):
    __tablename__ = "roles"
    __table_args__ = (
        # Name unique per tenant (NULL tenant = global/superadmin role)
        UniqueConstraint("name", "tenant_id", name="uq_roles_name_tenant"),
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )

    users: Mapped[list["User"]] = relationship(
        "User", secondary=user_roles, back_populates="roles"
    )
