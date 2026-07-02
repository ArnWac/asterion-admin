"""Platform-tier RBAC — the public-schema role store (ADR-0004).

Symmetric to tenant RBAC (:mod:`asterion.models.tenant_rbac`) but for the
**platform** tier: roles that carry ``platform.*`` permission keys and are
administered by a superadmin, never by a tenant owner. These tables live in the
**public** schema (global), so a role and its grants are visible across the
whole deployment, not scoped to one tenant.

Unlike the tenant side there is no membership indirection: a platform role is
linked directly to a global :class:`~asterion.models.user.User` via
:class:`PlatformUserRole` (a user is a platform operator outright, not "a member
of a tenant with a role"). ``is_superadmin`` remains the god-mode shorthand for
"holds ``platform.*``"; these tables express the *graded* staff below it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from asterion.models.base import GUID, GlobalModel

__all__ = [
    "PlatformRole",
    "PlatformRolePermission",
    "PlatformUserRole",
]


class PlatformRole(GlobalModel):
    """A platform-tier role. Public-schema; carries ``platform.*`` keys."""

    __tablename__ = "platform_roles"
    __table_args__ = (UniqueConstraint("name", name="uq_platform_roles_name"),)

    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    permissions: Mapped[list[PlatformRolePermission]] = relationship(
        "PlatformRolePermission",
        back_populates="role",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class PlatformRolePermission(GlobalModel):
    """A ``platform.*`` permission key assigned to a platform role."""

    __tablename__ = "platform_role_permissions"
    __table_args__ = (
        UniqueConstraint(
            "role_id",
            "permission_key",
            name="uq_platform_role_permission_key",
        ),
    )

    role_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("platform_roles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    permission_key: Mapped[str] = mapped_column(String(200), nullable=False, index=True)

    role: Mapped[PlatformRole] = relationship(
        "PlatformRole",
        back_populates="permissions",
    )


class PlatformUserRole(GlobalModel):
    """Direct link from a global user to a platform role (no membership).

    ``user_id`` references ``public.users.id``. Assigning a row makes the user a
    platform operator with that role's ``platform.*`` grants at shared scope.
    """

    __tablename__ = "platform_user_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uq_platform_user_role"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("platform_roles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    role: Mapped[PlatformRole] = relationship("PlatformRole")
