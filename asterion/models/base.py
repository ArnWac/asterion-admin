# asterion/models/base.py

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import CHAR, TypeDecorator


class GUID(TypeDecorator):
    """Platform-independent UUID type.

    Uses PostgreSQL UUID when available, otherwise stores UUID as CHAR(32).
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(32))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(str(value))
        if dialect.name == "postgresql":
            return value
        return value.hex

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))


def make_global_metadata(schema: str | None = "public") -> MetaData:
    """Create metadata for global/public tables."""
    return MetaData(schema=schema)


def make_tenant_metadata() -> MetaData:
    """Create metadata for tenant-local tables.

    Tenant-local tables intentionally do not define a fixed schema.
    They are created/queried inside the active tenant schema.
    """
    return MetaData()


GLOBAL_METADATA = make_global_metadata("public")
TENANT_METADATA = make_tenant_metadata()


class GlobalBase(DeclarativeBase):
    metadata = GLOBAL_METADATA


class TenantBase(DeclarativeBase):
    metadata = TENANT_METADATA


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class GlobalModel(IdMixin, TimestampMixin, GlobalBase):
    __abstract__ = True


class TenantModel(IdMixin, TimestampMixin, TenantBase):
    __abstract__ = True
