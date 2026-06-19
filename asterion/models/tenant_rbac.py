from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from asterion.models.base import GUID, TenantModel

__all__ = [
    "TenantMembershipRole",
    "TenantRole",
    "TenantRolePermission",
]


class TenantRole(TenantModel):
    """Tenant-local role.

    This table lives inside each tenant schema.
    Therefore it does not have a tenant_id column.
    """

    __tablename__ = "tenant_roles"
    __table_args__ = (UniqueConstraint("name", name="uq_tenant_roles_name"),)

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    description: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    is_system: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    permissions: Mapped[list[TenantRolePermission]] = relationship(
        "TenantRolePermission",
        back_populates="role",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    memberships: Mapped[list[TenantMembershipRole]] = relationship(
        "TenantMembershipRole",
        back_populates="role",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class TenantRolePermission(TenantModel):
    """Tenant-local permission assigned to a tenant-local role."""

    __tablename__ = "tenant_role_permissions"
    __table_args__ = (
        UniqueConstraint(
            "role_id",
            "permission_key",
            name="uq_tenant_role_permission_key",
        ),
    )

    role_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("tenant_roles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    permission_key: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        index=True,
    )

    role: Mapped[TenantRole] = relationship(
        "TenantRole",
        back_populates="permissions",
    )


class TenantMembershipRole(TenantModel):
    """Tenant-local mapping from global TenantMembership to tenant-local role.

    membership_id stores public TenantMembership.id.

    No cross-schema foreign key to public.tenant_memberships in v1.
    """

    __tablename__ = "tenant_membership_roles"
    __table_args__ = (
        UniqueConstraint(
            "membership_id",
            "role_id",
            name="uq_tenant_membership_role",
        ),
    )

    membership_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        nullable=False,
        index=True,
    )

    role_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("tenant_roles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    role: Mapped[TenantRole] = relationship(
        "TenantRole",
        back_populates="memberships",
    )
