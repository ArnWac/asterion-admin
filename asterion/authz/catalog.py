"""Derive permission keys from a registry and sync them into PermissionCatalog.

Without a populated catalog, tenant bootstrap can only seed an ``admin.*``
fallback for the ``owner`` role — the ``admin`` and ``viewer`` roles end up
empty. Running :func:`sync_permission_catalog` after registering all
ModelAdmins gives bootstrap the keys it needs to assign meaningful defaults.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asterion.authz.registry import PermissionRegistry
from asterion.models.permission_catalog import PermissionCatalog
from asterion.registry import AdminRegistry, ModelAdmin
from asterion.security.validation import (
    validate_action_name,
    validate_permission_key,
    validate_resource_name,
)

REGISTRY_SOURCE = "registry"
DEFAULT_CRUD_ACTIONS: tuple[str, ...] = ("list", "read", "create", "update", "delete")


@dataclass(frozen=True)
class SyncResult:
    added: int
    removed: int
    kept: int

    @property
    def total(self) -> int:
        return self.added + self.kept


def _crud_keys(resource: str) -> list[str]:
    resource = validate_resource_name(resource)
    return [f"admin.{resource}.{action}" for action in DEFAULT_CRUD_ACTIONS]


def _action_keys(resource: str, admin: ModelAdmin) -> list[str]:
    resource = validate_resource_name(resource)
    keys: list[str] = []
    for action in admin.actions:
        name = getattr(action, "name", None)
        if not name:
            continue
        try:
            validated = validate_action_name(name)
        except Exception:
            continue
        keys.append(f"admin.{resource}.{validated}")
    return keys


def generate_permission_keys(
    registry: AdminRegistry,
    permission_registry: PermissionRegistry | None = None,
) -> set[str]:
    """Return every permission key the catalog should expose.

    Combines two sources:

    * **Auto-derived CRUD keys** — for each registered ``ModelAdmin``,
      five keys (``admin.<resource>.{list,read,create,update,delete}``)
      plus one ``admin.<resource>.<action>`` per declared admin action.
    * **Extension-contributed keys** — if ``permission_registry`` is
      passed, every key that any extension registered via
      ``register_permissions(...)`` is also merged in. This is how
      e.g. ``oauth.identities.list`` from an OAuth extension lands in
      the catalog and becomes assignable to tenant roles.

    Wildcards are NOT emitted into the catalog. Wildcards are only valid
    as *granted* permission patterns (``admin.*`` on the owner role);
    the catalog is the list of concrete permissions a UI might display.
    """
    keys: set[str] = set()
    for admin in registry.all():
        resource = admin.model_name
        for key in _crud_keys(resource):
            keys.add(validate_permission_key(key))
        for key in _action_keys(resource, admin):
            keys.add(validate_permission_key(key))
    if permission_registry is not None:
        # PermissionRegistry already validated each key on register().
        keys.update(permission_registry.all())
    return keys


def _category_for(key: str) -> str | None:
    """``admin.<resource>.<action>`` → resource."""
    parts = key.split(".")
    if len(parts) == 3:
        return parts[1]
    return None


async def sync_permission_catalog(
    session: AsyncSession,
    desired_keys: Iterable[str],
    *,
    source: str = REGISTRY_SOURCE,
    prune: bool = True,
) -> SyncResult:
    """Insert missing keys, optionally prune stale ones, leave others alone.

    Pruning only touches rows whose ``source`` matches ``source``. Rows
    inserted by tests, by other apps, or by manual SQL stay untouched.
    """
    desired = {validate_permission_key(k) for k in desired_keys}

    existing_rows = (
        (await session.execute(select(PermissionCatalog).where(PermissionCatalog.source == source)))
        .scalars()
        .all()
    )
    existing_by_key = {row.key: row for row in existing_rows}
    existing_keys = set(existing_by_key.keys())

    to_add = desired - existing_keys
    to_remove = existing_keys - desired if prune else set()
    kept = desired & existing_keys

    for key in to_add:
        session.add(
            PermissionCatalog(
                key=key,
                category=_category_for(key),
                source=source,
            )
        )

    for key in to_remove:
        await session.delete(existing_by_key[key])

    await session.flush()
    return SyncResult(
        added=len(to_add),
        removed=len(to_remove),
        kept=len(kept),
    )


async def load_permission_keys(session: AsyncSession) -> set[str]:
    """Return every key currently stored in PermissionCatalog (any source)."""
    rows = (await session.execute(select(PermissionCatalog.key))).scalars().all()
    return set(rows)
