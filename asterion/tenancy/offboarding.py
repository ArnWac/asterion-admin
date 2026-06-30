"""Tenant offboarding (roadmap G6) — export, public cleanup, schema drop.

``tenant disable`` only flips ``is_active``; it leaves the schema, the public
membership/audit rows, and all tenant data in place. An AVV / DPA obligation
(return + erasure at contract end) needs the full lifecycle, which this module
provides:

1. **Export** a JSON-serialisable bundle — the tenant's public metadata +
   memberships, and (PostgreSQL) a complete dump of every table in the tenant
   schema. This is the "Rückgabe" half of the obligation and is produced for
   *both* modes before anything is deleted.
2. **Cleanup** the public rows that carry the tenant's id — memberships, audit,
   impersonation logs, saved filters.
3. **Drop** the tenant schema with ``DROP SCHEMA … CASCADE`` (PostgreSQL only;
   on SQLite the tenant has no private schema, so this step is skipped).
4. **Audit** the operation to the public log with a PII-free summary.

``mode="archive"`` keeps the ``Tenant`` row as a tombstone (``is_active=False``
+ ``offboarded_at`` set) so the slug stays reserved and the fact it existed is
on record. ``mode="drop"`` deletes the ``Tenant`` row too, freeing the slug.

On PostgreSQL the public cleanup, schema drop, tenant-row update/delete, and
audit row all run in **one transaction** (DDL is transactional in PostgreSQL),
so a failure leaves the tenant fully intact. Idempotent: re-running over an
already-offboarded tenant drops nothing new (``DROP SCHEMA IF EXISTS``, empty
deletes) and a ``drop``-ed slug is simply not found.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import CursorResult, delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from asterion.audit.service import TENANT_OFFBOARD
from asterion.db.session import DatabaseManager
from asterion.models.audit_log import AuditLog
from asterion.models.impersonation_log import ImpersonationLog
from asterion.models.saved_filter import SavedFilter
from asterion.models.tenant import Tenant
from asterion.models.tenant_membership import TenantMembership
from asterion.security.validation import validate_tenant_slug
from asterion.tenancy.resolver import invalidate_tenant
from asterion.tenancy.schema_strategy import _validate_schema_name, set_search_path

OffboardMode = Literal["archive", "drop"]


class TenantNotFoundError(LookupError):
    """Raised when an offboard/export targets a slug with no ``Tenant`` row."""


def _jsonable(value: Any) -> Any:
    """Coerce a DB value into something ``json.dumps`` can serialise.

    Handles the column types asterion actually stores (UUID, datetime/date,
    Decimal, bytes); everything else passes through unchanged so JSON / dict
    payloads survive intact.
    """
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    if hasattr(value, "isoformat"):  # date / time
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    # Decimal and other numerics → str keeps full precision in JSON.
    if value.__class__.__module__ == "decimal":
        return str(value)
    return value


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {col.name: _jsonable(getattr(row, col.name)) for col in row.__table__.columns}


async def _dump_schema_tables(
    session: AsyncSession, schema_name: str
) -> dict[str, list[dict[str, Any]]]:
    """Dump every base table in ``schema_name`` as ``{table: [row-dicts]}``.

    PostgreSQL-only: enumerates the schema's tables from ``information_schema``
    (catalog-sourced names, not user input) and selects every row. The session
    must already point at a PostgreSQL connection; ``set_search_path`` is applied
    so unqualified inserts are safe, but each ``SELECT`` is schema-qualified.
    """
    table_names = (
        (
            await session.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :schema AND table_type = 'BASE TABLE' "
                    "ORDER BY table_name"
                ),
                {"schema": schema_name},
            )
        )
        .scalars()
        .all()
    )

    tables: dict[str, list[dict[str, Any]]] = {}
    for table_name in table_names:
        result = await session.execute(text(f'SELECT * FROM "{schema_name}"."{table_name}"'))
        tables[table_name] = [
            {k: _jsonable(v) for k, v in mapping.items()} for mapping in result.mappings().all()
        ]
    return tables


async def export_tenant(
    db: DatabaseManager,
    slug: str,
    *,
    include_schema_dump: bool = True,
) -> dict[str, Any]:
    """Build a JSON-serialisable export bundle for ``slug``.

    Always includes the public ``tenant`` metadata and its ``memberships``. On
    PostgreSQL (and when ``include_schema_dump``) it also dumps every table in
    the tenant schema under ``schema``. On SQLite tenant data lives in the shared
    schema with no per-tenant boundary, so ``schema`` is reported as ``None``
    with a note — an honest limitation, consistent with the rest of the
    framework (SQLite cannot isolate tenants).

    Raises :class:`TenantNotFoundError` if the slug has no ``Tenant`` row.
    """
    slug = validate_tenant_slug(slug)

    async with db.session() as session:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.slug == slug))
        ).scalar_one_or_none()
        if tenant is None:
            raise TenantNotFoundError(f"Tenant {slug!r} not found.")
        schema_name = tenant.schema_name
        bundle: dict[str, Any] = {
            "exported_at": datetime.now(UTC).isoformat(),
            "tenant": _row_to_dict(tenant),
            "memberships": [
                _row_to_dict(m)
                for m in (
                    await session.execute(
                        select(TenantMembership).where(TenantMembership.tenant_id == tenant.id)
                    )
                )
                .scalars()
                .all()
            ],
        }

    is_postgres = db.engine.dialect.name == "postgresql"
    if include_schema_dump and is_postgres:
        async with db.session() as session:
            async with session.begin():
                await set_search_path(session, schema_name)
                bundle["schema"] = {
                    "name": schema_name,
                    "tables": await _dump_schema_tables(session, schema_name),
                }
    else:
        bundle["schema"] = None
        if include_schema_dump and not is_postgres:
            bundle["schema_note"] = (
                "SQLite has no per-tenant schema; tenant tables share the public "
                "schema and cannot be dumped in isolation."
            )

    return bundle


async def _delete_public_rows(session: AsyncSession, tenant: Tenant) -> dict[str, int]:
    """Delete the public rows that carry this tenant's id. Returns per-table counts."""
    tenant_id = tenant.id

    def _count(result: Any) -> int:
        rowcount = cast("CursorResult[Any]", result).rowcount
        return rowcount if rowcount is not None else 0

    memberships = _count(
        await session.execute(
            delete(TenantMembership).where(TenantMembership.tenant_id == tenant_id)
        )
    )
    audit = _count(await session.execute(delete(AuditLog).where(AuditLog.tenant_id == tenant_id)))
    impersonations = _count(
        await session.execute(
            delete(ImpersonationLog).where(ImpersonationLog.tenant_id == tenant_id)
        )
    )
    # SavedFilter.tenant_id is the stringified tenant UUID (see saved_filter_router).
    saved_filters = _count(
        await session.execute(delete(SavedFilter).where(SavedFilter.tenant_id == str(tenant_id)))
    )
    return {
        "memberships": memberships,
        "audit": audit,
        "impersonations": impersonations,
        "saved_filters": saved_filters,
    }


async def offboard_tenant(
    db: DatabaseManager,
    slug: str,
    *,
    mode: OffboardMode = "archive",
    actor_user_id: uuid.UUID | None = None,
    include_schema_dump: bool = True,
) -> dict[str, Any]:
    """Export, clean up, and drop a tenant. Returns a summary incl. the export.

    Steps: export bundle → delete public rows → ``DROP SCHEMA … CASCADE``
    (PostgreSQL) → archive/drop the ``Tenant`` row → write an audit row. On
    PostgreSQL all mutations run in one transaction. ``mode="archive"`` keeps a
    tombstone ``Tenant`` row; ``mode="drop"`` deletes it.

    Raises :class:`TenantNotFoundError` if the slug has no ``Tenant`` row, and
    ``ValueError`` for an unknown ``mode``.
    """
    if mode not in ("archive", "drop"):
        raise ValueError(f"Unknown offboard mode: {mode!r} (expected 'archive' or 'drop').")
    slug = validate_tenant_slug(slug)

    # Export first — before any row is touched — so the bundle is complete.
    export = await export_tenant(db, slug, include_schema_dump=include_schema_dump)
    schema_name = export["tenant"]["schema_name"]
    _validate_schema_name(schema_name)

    is_postgres = db.engine.dialect.name == "postgresql"
    now = datetime.now(UTC)

    async with db.session() as session:
        async with session.begin():
            tenant = (
                await session.execute(select(Tenant).where(Tenant.slug == slug))
            ).scalar_one_or_none()
            if tenant is None:
                raise TenantNotFoundError(f"Tenant {slug!r} not found.")
            tenant_id = tenant.id

            deleted = await _delete_public_rows(session, tenant)

            if is_postgres:
                await session.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))

            if mode == "drop":
                await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
            else:  # archive
                tenant.is_active = False
                tenant.offboarded_at = now

            # PII-free summary — slug/schema/counts only, never tenant data.
            session.add(
                AuditLog(
                    method="INTERNAL",
                    path="/tenancy/offboard",
                    status_code=0,
                    action=TENANT_OFFBOARD,
                    actor_user_id=actor_user_id,
                    changes={
                        "slug": slug,
                        "schema_name": schema_name,
                        "mode": mode,
                        "schema_dropped": is_postgres,
                        "public_rows_deleted": deleted,
                    },
                )
            )

    # Same-process cache: the slug must stop resolving (drop) / start 403-ing
    # (archive) immediately, not after the TTL.
    invalidate_tenant(slug)

    return {
        "slug": slug,
        "schema_name": schema_name,
        "mode": mode,
        "schema_dropped": is_postgres,
        "public_rows_deleted": deleted,
        "export": export,
    }
