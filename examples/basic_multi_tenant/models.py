from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from adminfoundry.models.base import TimestampedBase


class Task(TimestampedBase):
    __tablename__ = "tasks"

    title:     Mapped[str] = mapped_column(String(255), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), nullable=True, index=True)
