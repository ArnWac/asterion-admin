from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adminfoundry.models.tenant import Tenant
    from adminfoundry.models.tenant_membership import TenantMembership
    from adminfoundry.tenancy.tenant_models import TenantRole


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
    def from_orm(cls, tenant: "Tenant") -> "TenantContext":
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
    def from_dict(cls, data: dict) -> "TenantContext":
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


@dataclass(slots=True)
class TenantAuthContext:
    """Per-request tenant authorization context built after membership is verified.

    Holds the resolved tenant, the public-schema membership record, and the
    tenant-local roles/permission_keys loaded from the active tenant schema.
    None of the role data touches public.roles or User.roles.
    """

    tenant: "TenantContext"
    membership: "TenantMembership"
    roles: "list[TenantRole]"
    permission_keys: set[str]

    def has_permission(self, key: str) -> bool:
        return key in self.permission_keys

    def has_role(self, name: str) -> bool:
        return any(r.name == name for r in self.roles)

    def role_names(self) -> frozenset[str]:
        return frozenset(r.name for r in self.roles)
