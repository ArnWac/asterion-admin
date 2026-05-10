import uuid
from sqlalchemy import String, Column, ForeignKey, Table, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from adminfoundry.models.base import TimestampedBase, Base, GUID

# Association table — no ORM class needed for this phase
user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", GUID, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", GUID, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)


class Role(TimestampedBase):
    __tablename__ = "roles"
    __table_args__ = (
        # Name unique per tenant (NULL tenant = global/superadmin role)
        UniqueConstraint("name", "tenant_id", name="uq_roles_name_tenant"),
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )
