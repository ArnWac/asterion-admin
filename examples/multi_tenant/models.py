"""Tenant-scoped models for the multi-tenant issue-tracker demo.

Both ``Project`` and ``Ticket`` inherit from :class:`TenantModel`, which
means their tables live inside each tenant's PostgreSQL schema. There is
no ``tenant_id`` column — isolation is enforced by ``SET LOCAL
search_path`` applied by ``TenantMiddleware`` on every request.
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from adminfoundry.models.base import GUID, TenantModel


class TicketStatus(enum.StrEnum):
    open = "open"
    in_progress = "in_progress"
    closed = "closed"


class TicketPriority(enum.StrEnum):
    low = "low"
    normal = "normal"
    high = "high"
    urgent = "urgent"


class TicketCategory(enum.StrEnum):
    bug = "bug"
    feature = "feature"
    chore = "chore"


class TicketComponent(enum.StrEnum):
    # bug
    api = "api"
    ui = "ui"
    db = "db"
    # feature
    integration = "integration"
    reporting = "reporting"
    # chore
    ci = "ci"
    docs = "docs"


class Project(TenantModel):
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    tickets: Mapped[list[Ticket]] = relationship(
        "Ticket",
        back_populates="project",
        cascade="all, delete-orphan",
    )


class Ticket(TenantModel):
    __tablename__ = "tickets"

    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[TicketStatus] = mapped_column(
        SAEnum(TicketStatus, name="ticket_status"),
        nullable=False,
        default=TicketStatus.open,
    )
    priority: Mapped[TicketPriority] = mapped_column(
        SAEnum(TicketPriority, name="ticket_priority"),
        nullable=False,
        default=TicketPriority.normal,
    )
    assignee: Mapped[str | None] = mapped_column(String(255), nullable=True)

    category: Mapped[TicketCategory] = mapped_column(
        SAEnum(TicketCategory, name="ticket_category"),
        nullable=False,
        default=TicketCategory.bug,
    )
    # Narrowed by ``category`` via ``field_dependencies`` in admin_config.
    component: Mapped[TicketComponent | None] = mapped_column(
        SAEnum(TicketComponent, name="ticket_component"),
        nullable=True,
    )
    # Shown in the form only when status == "closed" (field_conditions).
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Never serialized / accepted — listed in protected_fields.
    secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)

    project: Mapped[Project] = relationship("Project", back_populates="tickets")
