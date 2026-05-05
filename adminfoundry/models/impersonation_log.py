import uuid
from datetime import datetime
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column
from adminfoundry.models.base import Base, GUID, utcnow


class ImpersonationLog(Base):
    __tablename__ = "impersonation_logs"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    superadmin_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)
    target_user_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    jti: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
