from __future__ import annotations

import uuid

from sqlalchemy import JSON, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from asterion.models.base import GUID, TenantModel


class TenantAuditLog(TenantModel):
    """Per-tenant audit trail (lives inside each tenant schema).

    Mirrors :class:`~asterion.models.audit_log.AuditLog` but without a
    ``tenant_id`` column — the schema *is* the tenant, so isolation is
    physical (``SET search_path``) rather than a discriminator column.

    Tenant-context admin events (CRUD / actions / import-export on
    tenant-scoped resources) are routed here by the audit writer; global
    and cross-tenant events (login, impersonation, user/tenant
    management) stay in the public ``audit_logs`` table. This keeps a
    tenant's audit trail physically isolated and queryable through the
    normal tenant ``search_path`` — no cross-tenant leak risk, no filter.
    """

    __tablename__ = "tenant_audit_logs"
    __table_args__ = (
        Index("ix_tenant_audit_logs_created_at", "created_at"),
        Index(
            "ix_tenant_audit_logs_actor_user_id_created_at",
            "actor_user_id",
            "created_at",
        ),
        Index("ix_tenant_audit_logs_action_created_at", "action", "created_at"),
        Index("ix_tenant_audit_logs_record_id", "record_id"),
    )

    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(),
        nullable=True,
        index=True,
    )

    resource: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    record_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    action: Mapped[str | None] = mapped_column(String(100), nullable=True)

    actor_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    changes: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
