"""Minimal PermissionRegistry — in-memory collector for permission keys.

Phase 5 of the v1-providers refactor introduces this as a container
only — extensions can register keys via the ``register_permissions``
hook, but the framework does not yet sync them into
``PermissionCatalog`` (the DB-side store used by tenant-RBAC).

Phase 6 will:

* Have :func:`asterion.authz.catalog.sync_permission_catalog` merge
  registry keys with the auto-derived CRUD keys before sync, so extension
  permissions land in the catalog and become assignable to tenant roles.
* Optionally surface the registry contents via the contract endpoint
  for UI rendering.

For now the registry exists so the extension SPI is complete — every
extension hook the prompt names has somewhere to write to.
"""

from __future__ import annotations

from collections.abc import Iterable

from asterion.extensions.errors import RegistryFrozenError
from asterion.security.validation import validate_permission_key


class PermissionRegistry:
    """Holds permission keys contributed by extensions.

    Use :func:`asterion.security.validation.validate_permission_key`
    semantics: ``namespace.<resource>.<action>`` shape. Keys are
    validated on registration so a typo fails early.
    """

    __slots__ = ("_frozen", "_keys")

    def __init__(self, *, defaults: Iterable[str] = ()) -> None:
        self._keys: set[str] = {validate_permission_key(k) for k in defaults}
        self._frozen = False

    def register(self, *keys: str) -> None:
        if self._frozen:
            raise RegistryFrozenError(
                "PermissionRegistry is frozen — extensions must register "
                "permission keys during register_permissions(), not later."
            )
        for raw in keys:
            self._keys.add(validate_permission_key(raw))

    def freeze(self) -> None:
        self._frozen = True

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key in self._keys

    def all(self) -> frozenset[str]:
        return frozenset(self._keys)
