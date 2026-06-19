from __future__ import annotations

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from asterion.models.base import GlobalModel


class PermissionCatalog(GlobalModel):
    """Global registry of permission keys known to the framework.

    Tenant-local role permissions store permission_key strings.
    This catalog is for discovery/documentation/seeding, not authorization itself.
    """

    __tablename__ = "permission_catalog"
    __table_args__ = (Index("ix_permission_catalog_key", "key", unique=True),)

    key: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        unique=True,
    )

    description: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    category: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    source: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )
