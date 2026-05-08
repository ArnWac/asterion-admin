import uuid
from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from adminfoundry.models.base import TimestampedBase, GUID


class RolePermission(TimestampedBase):
    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "model_name", name="uq_role_permission"),)

    role_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    can_list: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_create: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_update: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_delete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
