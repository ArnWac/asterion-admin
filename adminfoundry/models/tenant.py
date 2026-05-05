from sqlalchemy import String, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from adminfoundry.models.base import TimestampedBase


class Tenant(TimestampedBase):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(63), unique=True, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    @property
    def schema_name(self) -> str:
        return f"tenant_{self.slug}"
