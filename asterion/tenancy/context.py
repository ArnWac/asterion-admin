from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from asterion.models.tenant import Tenant


# Schema name of the tenant resolved for the current request, or None when no
# tenant is in scope (out-of-request work, or a request that resolved no
# tenant). ``get_async_session`` sets this for the duration of the request
# transaction; ``independent_tenant_session`` reads it to scope a *separate*
# transaction to the same tenant. Set once per request and reset on teardown,
# so it never leaks onto the next request reusing the same worker task.
current_tenant_schema: ContextVar[str | None] = ContextVar(
    "current_tenant_schema", default=None
)


@dataclass
class TenantContext:
    """Immutable snapshot of a resolved tenant attached to request.state.tenant.

    Strict superset of the SimpleNamespace shape previously produced by
    middleware/tenant.py._deserialize().  All call sites that read attributes
    from request.state.tenant are satisfied by this type.
    """

    id: uuid.UUID
    slug: str
    name: str
    is_active: bool
    schema_name: str
    timezone: str | None = None
    language: str | None = None
    date_format: str | None = None
    date_pattern: str | None = None
    allowed_cidrs: str | None = None
    is_superadmin_context: bool = False

    @classmethod
    def from_orm(cls, tenant: Tenant) -> TenantContext:
        return cls(
            id=tenant.id,
            slug=tenant.slug,
            name=tenant.name,
            is_active=tenant.is_active,
            schema_name=tenant.schema_name,
            timezone=tenant.timezone,
            language=tenant.language,
            date_format=tenant.date_format,
            date_pattern=tenant.date_pattern,
            allowed_cidrs=tenant.allowed_cidrs,
        )

    @classmethod
    def from_dict(cls, data: dict) -> TenantContext:
        return cls(
            id=uuid.UUID(data["id"]),
            slug=data["slug"],
            name=data["name"],
            is_active=data["is_active"],
            schema_name=f"tenant_{data['slug']}",
            timezone=data.get("timezone"),
            language=data.get("language"),
            date_format=data.get("date_format"),
            date_pattern=data.get("date_pattern"),
            allowed_cidrs=data.get("allowed_cidrs"),
        )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "slug": self.slug,
            "name": self.name,
            "is_active": self.is_active,
            "timezone": self.timezone,
            "language": self.language,
            "date_format": self.date_format,
            "date_pattern": self.date_pattern,
            "allowed_cidrs": self.allowed_cidrs,
        }
