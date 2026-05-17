from __future__ import annotations
import uuid
from typing import TYPE_CHECKING
from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from adminfoundry.models.base import GUID, TimestampedBase

if TYPE_CHECKING:
    from adminfoundry.models.role import Role
    from adminfoundry.models.tenant import Tenant
    from adminfoundry.models.user import User


class TenantMembership(TimestampedBase):
    """Records that a user belongs to a tenant with a specific active state.

    Tenant-scoped roles are assigned through this membership (via membership_roles),
    never directly on the User. This is the authorization pivot for all tenant-scoped
    requests.
    """
    __tablename__ = "tenant_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id", name="uq_membership_user_tenant"),
        Index("ix_membership_tenant_active", "tenant_id", "is_active"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Optional tenant-local profile fields
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="memberships")
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="memberships")
    roles: Mapped[list["Role"]] = relationship(
        "Role",
        secondary="membership_roles",
        back_populates="memberships",
        lazy="selectin",
    )
