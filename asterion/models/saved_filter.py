"""Saved-filter storage model.

Persists one named list-view filter configuration per user per
resource. Scoped by ``tenant_id`` (nullable; ``None`` means
"public / root scope") so a multi-tenant deployment naturally keeps
each tenant's saved filters separate.

``user_id`` is a plain string column rather than a foreign key — the
v1-providers refactor allows external user providers where the user
table need not exist in asterion's own DB. Storing the principal
id as an opaque string keeps the model compatible with every
provider implementation.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from asterion.models.base import GlobalModel


class SavedFilter(GlobalModel):
    __tablename__ = "admin_saved_filters"

    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resource: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (Index("ix_saved_filters_owner", "user_id", "resource"),)
