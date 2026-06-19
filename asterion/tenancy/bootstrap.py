"""Tenant bootstrap orchestration.

Public functions are idempotent — re-running them on an existing tenant does
not create duplicate rows. Schema provisioning is PostgreSQL-only (the v1
multi-tenant strategy is schema-per-tenant). Seeding logic is DB-agnostic so
it can be exercised against SQLite in unit tests.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from asterion.authz.registry import PermissionRegistry
    from asterion.registry import AdminRegistry

from asterion.db.session import DatabaseManager
from asterion.models.audit_log import AuditLog
from asterion.models.permission_catalog import PermissionCatalog
from asterion.models.tenant import Tenant
from asterion.models.tenant_membership import TenantMembership
from asterion.models.tenant_rbac import (
    TenantMembershipRole,
    TenantRole,
    TenantRolePermission,
)
from asterion.models.user import User
from asterion.security.validation import (
    validate_permission_key,
    validate_tenant_slug,
)
from asterion.tenancy.schema_names import make_tenant_schema_name
from asterion.tenancy.schema_strategy import get_tenant_session

_DEFAULT_ROLE_DEFS: tuple[dict, ...] = (
    {"name": "owner", "description": "Full tenant access", "is_system": True},
    {"name": "admin", "description": "Administrative access", "is_system": True},
    {"name": "viewer", "description": "Read-only access", "is_system": True},
)

_OWNER_FALLBACK_PERMISSIONS: frozenset[str] = frozenset({"admin.*"})
_ADMIN_PERMISSIONS_DENY: frozenset[str] = frozenset(
    {"admin.audit_logs.delete", "admin.users.delete"}
)


# ---------------------------------------------------------------------------
# Public schema helpers (DB-agnostic, idempotent)
# ---------------------------------------------------------------------------


async def create_tenant_record(
    public_db: AsyncSession,
    *,
    name: str,
    slug: str,
) -> Tenant:
    """Create or fetch the public Tenant row for ``slug``."""
    slug = validate_tenant_slug(slug)
    schema_name = make_tenant_schema_name(slug)

    existing = (
        await public_db.execute(select(Tenant).where(Tenant.slug == slug))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    tenant = Tenant(name=name, slug=slug, schema_name=schema_name, is_active=True)
    public_db.add(tenant)
    await public_db.flush()
    return tenant


async def assign_owner_membership(
    public_db: AsyncSession,
    *,
    tenant: Tenant,
    user: User,
) -> TenantMembership:
    """Create or fetch the public TenantMembership row for ``(user, tenant)``."""
    existing = (
        await public_db.execute(
            select(TenantMembership)
            .where(TenantMembership.user_id == user.id)
            .where(TenantMembership.tenant_id == tenant.id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        if not existing.is_active:
            existing.is_active = True
        return existing

    membership = TenantMembership(user_id=user.id, tenant_id=tenant.id, is_active=True)
    public_db.add(membership)
    await public_db.flush()
    return membership


# ---------------------------------------------------------------------------
# Tenant-local seeding (DB-agnostic, idempotent)
# ---------------------------------------------------------------------------


async def seed_default_tenant_roles(
    tenant_db: AsyncSession,
    public_db: AsyncSession,
    owner_membership_id: uuid.UUID | None = None,
) -> dict[str, TenantRole]:
    """Create default tenant-local roles + role permissions.

    Idempotent: re-running on an existing tenant only adds missing rows.
    Owner role always receives at least ``admin.*``. Admin role receives
    every key from ``PermissionCatalog`` minus a deny list. Viewer role
    receives every catalog key ending in ``.list``.

    Returns a mapping ``role_name → TenantRole``.
    """
    catalog_keys = {
        validate_permission_key(key)
        for key in (await public_db.execute(select(PermissionCatalog.key))).scalars().all()
    }

    role_map: dict[str, TenantRole] = {}
    for role_def in _DEFAULT_ROLE_DEFS:
        existing_role = (
            await tenant_db.execute(select(TenantRole).where(TenantRole.name == role_def["name"]))
        ).scalar_one_or_none()
        if existing_role is None:
            role = TenantRole(**role_def)
            tenant_db.add(role)
            await tenant_db.flush()
        else:
            role = existing_role
        role_map[role_def["name"]] = role

    owner_keys = set(_OWNER_FALLBACK_PERMISSIONS) | catalog_keys
    admin_keys = {k for k in catalog_keys if k not in _ADMIN_PERMISSIONS_DENY}
    viewer_keys = {k for k in catalog_keys if k.endswith(".list")}

    await _grant_permissions(tenant_db, role_map["owner"], owner_keys)
    await _grant_permissions(tenant_db, role_map["admin"], admin_keys)
    await _grant_permissions(tenant_db, role_map["viewer"], viewer_keys)

    if owner_membership_id is not None:
        await _assign_membership_role(
            tenant_db,
            membership_id=owner_membership_id,
            role=role_map["owner"],
        )

    await tenant_db.flush()
    return role_map


async def _grant_permissions(tenant_db: AsyncSession, role: TenantRole, keys: set[str]) -> None:
    if not keys:
        return
    existing_keys = set(
        (
            await tenant_db.execute(
                select(TenantRolePermission.permission_key).where(
                    TenantRolePermission.role_id == role.id
                )
            )
        )
        .scalars()
        .all()
    )
    for key in keys - existing_keys:
        tenant_db.add(TenantRolePermission(role_id=role.id, permission_key=key))


async def _assign_membership_role(
    tenant_db: AsyncSession,
    *,
    membership_id: uuid.UUID,
    role: TenantRole,
) -> None:
    existing = (
        await tenant_db.execute(
            select(TenantMembershipRole)
            .where(TenantMembershipRole.membership_id == membership_id)
            .where(TenantMembershipRole.role_id == role.id)
        )
    ).scalar_one_or_none()
    if existing is None:
        tenant_db.add(TenantMembershipRole(membership_id=membership_id, role_id=role.id))


# ---------------------------------------------------------------------------
# PostgreSQL-only schema provisioning + full bootstrap
# ---------------------------------------------------------------------------


async def provision_tenant_schema(
    public_db: AsyncSession,
    *,
    schema_name: str,
) -> None:
    """``CREATE SCHEMA IF NOT EXISTS`` on the current public session."""
    await public_db.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))


def _run_tenant_migrations(schema_name: str) -> None:
    ini_path = Path(__file__).parent.parent.parent / "alembic_tenant.ini"
    if not ini_path.exists():
        raise FileNotFoundError(f"alembic_tenant.ini not found at {ini_path}")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(ini_path),
            "-x",
            f"schema={schema_name}",
            "upgrade",
            "head",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Tenant migration failed for schema {schema_name!r}:\n{result.stderr}")


async def bootstrap_tenant(
    slug: str,
    public_db: AsyncSession,
    *,
    owner_membership_id: uuid.UUID | None = None,
    database_url: str,
    registry: AdminRegistry | None = None,
    permission_registry: PermissionRegistry | None = None,
) -> None:
    """Provision and seed a tenant. PostgreSQL only — no-op otherwise.

    Steps (idempotent):
      1. (Optional) Sync ``PermissionCatalog`` from ``registry`` (admin
         CRUD/action keys) and ``permission_registry`` (extension-
         contributed keys) so the seed step finds every permission the
         app exposes. Without it, admin/viewer roles end up empty and
         owner gets only the ``admin.*`` fallback.
      2. Create the tenant schema (CREATE SCHEMA IF NOT EXISTS).
      3. Run tenant Alembic migrations against the schema.
      4. Open a new session scoped to the tenant schema.
      5. Seed default roles + role permissions.
      6. Optionally assign the owner membership to the owner role.
      7. Write an audit log entry.
    """
    if "postgresql" not in database_url:
        return

    slug = validate_tenant_slug(slug)
    schema_name = make_tenant_schema_name(slug)

    if registry is not None or permission_registry is not None:
        # Local import to avoid circular import at module load.
        from asterion.authz.catalog import (
            generate_permission_keys,
            sync_permission_catalog,
        )

        # ``generate_permission_keys`` derives CRUD/action keys from the
        # AdminRegistry and merges the extension PermissionRegistry. Use
        # an empty AdminRegistry when only the permission_registry is
        # given (extension keys only, no auto-derived CRUD).
        from asterion.registry import AdminRegistry as _Registry

        admin_reg = registry if registry is not None else _Registry()
        keys = generate_permission_keys(admin_reg, permission_registry)
        if keys:
            await sync_permission_catalog(public_db, keys)
            await public_db.flush()

    await provision_tenant_schema(public_db, schema_name=schema_name)
    await public_db.commit()

    _run_tenant_migrations(schema_name)

    db = DatabaseManager(database_url)
    try:
        async for tenant_db in get_tenant_session(schema_name, db):
            await seed_default_tenant_roles(tenant_db, public_db, owner_membership_id)
    finally:
        await db.dispose()

    public_db.add(
        AuditLog(
            method="INTERNAL",
            path="/tenancy/bootstrap",
            status_code=0,
            action="tenant.created",
            changes={"schema_name": schema_name},
        )
    )
    await public_db.commit()
