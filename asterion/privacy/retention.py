"""Audit-log retention (roadmap G3).

``asterion audit prune`` historically deleted only the **public** ``audit_logs``
table; the per-tenant ``tenant_audit_logs`` (one inside each tenant schema) grew
unbounded — a storage-limitation gap (Art. 5) per tenant. This module prunes
**both**: the public table once, then each tenant's table by switching
``search_path`` to its schema (PostgreSQL). The cutoff is ``now - retention_days``.

On SQLite there are no per-tenant schemas, so only the public table is pruned
(the per-tenant sweep is a PostgreSQL operation — :class:`TenantAuditLog` lives
inside the schema).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from asterion.db.session import DatabaseManager
from asterion.models.audit_log import AuditLog
from asterion.models.tenant import Tenant
from asterion.models.tenant_audit_log import TenantAuditLog
from asterion.models.user import User
from asterion.privacy.anonymizer import (
    ANONYMIZED_EMAIL_DOMAIN,
    anonymize_audit_actor,
    anonymize_user,
)
from asterion.tenancy.schema_strategy import set_search_path


def retention_cutoff(retention_days: int, *, now: datetime | None = None) -> datetime:
    """The timestamp before which audit rows are eligible for pruning."""
    return (now or datetime.now(UTC)) - timedelta(days=retention_days)


async def _prune(session: AsyncSession, model: type[Any], cutoff: datetime) -> int:
    result = await session.execute(delete(model).where(model.created_at < cutoff))
    rowcount = cast("CursorResult[Any]", result).rowcount
    return rowcount if rowcount is not None else 0


async def prune_public_audit(session: AsyncSession, *, cutoff: datetime) -> int:
    """Delete public ``audit_logs`` rows older than ``cutoff``. Returns the count."""
    return await _prune(session, AuditLog, cutoff)


async def prune_tenant_audit(session: AsyncSession, *, cutoff: datetime) -> int:
    """Delete ``tenant_audit_logs`` rows older than ``cutoff`` in the schema the
    session's ``search_path`` currently points at. Returns the count."""
    return await _prune(session, TenantAuditLog, cutoff)


async def _anonymize_expired_users(db: DatabaseManager, *, cutoff: datetime) -> list[uuid.UUID]:
    """Anonymise every still-PII user whose ``deactivated_at`` is past ``cutoff``.

    Tombstones the user row + their **public** audit-actor PII; returns the ids
    so the per-tenant sweep can redact their actor PII inside each schema too.
    Already-anonymised users are skipped (their tombstone email excludes them),
    so the job is idempotent across runs.
    """
    anonymized: list[uuid.UUID] = []
    async with db.session() as session:
        async with session.begin():
            candidates = (
                (
                    await session.execute(
                        select(User).where(
                            User.is_active.is_(False),
                            User.deactivated_at.is_not(None),
                            User.deactivated_at < cutoff,
                            User.email.notlike(f"%@{ANONYMIZED_EMAIL_DOMAIN}"),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for user in candidates:
                uid = user.id
                anonymize_user(user)
                await anonymize_audit_actor(session, uid)
                anonymized.append(uid)
    return anonymized


async def apply_retention(
    db: DatabaseManager,
    *,
    retention_days: int,
    all_tenants: bool = True,
    user_anonymize_after_days: int | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Prune audit rows older than ``retention_days`` in public + each tenant schema.

    Returns ``{"public": n, "<slug>": n, ...}`` deleted-row counts. The per-tenant
    sweep (``all_tenants=True``) sets ``search_path`` to each tenant's schema and
    runs only on PostgreSQL; on SQLite just the public table is pruned. Each
    tenant is pruned in its own transaction so one failure doesn't roll back the
    others.

    When ``user_anonymize_after_days`` is set (G2 stage-2 retention), users
    deactivated longer ago than that are auto-anonymised first; the count lands
    under ``"anonymized_users"`` and their actor PII is redacted in the public
    audit and — on PostgreSQL — in every tenant schema during the sweep.
    """
    now_dt = now or datetime.now(UTC)
    cutoff = retention_cutoff(retention_days, now=now_dt)
    results: dict[str, int] = {}

    async with db.session() as session:
        async with session.begin():
            results["public"] = await prune_public_audit(session, cutoff=cutoff)

    anonymized_ids: list[uuid.UUID] = []
    if user_anonymize_after_days is not None:
        anon_cutoff = now_dt - timedelta(days=user_anonymize_after_days)
        anonymized_ids = await _anonymize_expired_users(db, cutoff=anon_cutoff)
        results["anonymized_users"] = len(anonymized_ids)

    is_postgres = db.engine.dialect.name == "postgresql"
    if not all_tenants or not is_postgres:
        return results

    async with db.session() as session:
        tenants = [
            (t.slug, t.schema_name) for t in (await session.execute(select(Tenant))).scalars().all()
        ]

    for slug, schema_name in tenants:
        async with db.session() as session:
            async with session.begin():
                await set_search_path(session, schema_name)
                results[slug] = await prune_tenant_audit(session, cutoff=cutoff)
                for uid in anonymized_ids:
                    await anonymize_audit_actor(session, uid, tenant_scoped=True)

    return results
