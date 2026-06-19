from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from asterion.models.base import GUID, GlobalModel

if TYPE_CHECKING:
    from asterion.models.tenant import Tenant
    from asterion.models.user import User


class TenantMembership(GlobalModel):
    """Global record that links a user to a tenant.

    Tenant-local roles are assigned via TenantMembershipRole inside the tenant schema.
    """

    __tablename__ = "tenant_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id", name="uq_membership_user_tenant"),
        Index("ix_membership_tenant_active", "tenant_id", "is_active"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)

    user: Mapped[User] = relationship(
        "User",
        back_populates="memberships",
    )

    tenant: Mapped[Tenant] = relationship(
        "Tenant",
        back_populates="memberships",
    )
