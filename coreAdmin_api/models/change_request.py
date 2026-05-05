import uuid
import enum
from datetime import datetime
from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from coreAdmin_api.models.base import Base, GUID, utcnow


class ChangeRequestStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    applied = "applied"
    reverted = "reverted"


class ChangeRequest(Base):
    __tablename__ = "change_requests"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    object_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    operation: Mapped[str] = mapped_column(String(20), nullable=False)  # create | update | delete

    requester_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ChangeRequestStatus.pending)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # JSON snapshots — no protected fields stored
    proposed_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_data: Mapped[str | None] = mapped_column(Text, nullable=True)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    audit_log_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
