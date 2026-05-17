from sqlalchemy import Column, ForeignKey, Table

from adminfoundry.models.base import Base, GUID

user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", GUID, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", GUID, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)

# Tenant-scoped role assignments go through TenantMembership, not directly on User.
membership_roles = Table(
    "membership_roles",
    Base.metadata,
    Column(
        "membership_id",
        GUID,
        ForeignKey("tenant_memberships.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "role_id",
        GUID,
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

# Forces TenantMembership into the SQLAlchemy mapper registry before configure_mappers() resolves string refs.
def _ensure_membership_registered() -> None:
    from adminfoundry.models import tenant_membership as _  # noqa: F401


_ensure_membership_registered()
