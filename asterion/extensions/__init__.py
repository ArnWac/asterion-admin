"""Adminfoundry extensions — opt-in, optionally-shipped framework add-ons.

An *extension* is a subclass of :class:`AdminExtension` that bundles
routes, permission keys, contract fragments, navigation items, and/or
async lifespan hooks. Apps opt in by passing instances to
:func:`asterion.create_admin`::

    from asterion import create_admin
    from asterion.extensions.import_export import ImportExportExtension

    app = create_admin(
        config=config,
        extensions=[ImportExportExtension()],
    )

The lifecycle is documented on :class:`AdminExtension`. The hard
architecture rule: **core code does not import concrete extensions**.
The boundary is enforced by ``tests/security/test_import_boundaries.py``.

Public surface:

* :class:`AdminExtension` — base class to subclass.
* :class:`ExtensionContext` — handle passed to every ``register_*`` hook.
* :class:`ExtensionRegistry` — collection on ``AdminRuntime.extensions``.
* Errors: :class:`ExtensionError`, :class:`DuplicateExtensionError`,
  :class:`RegistryFrozenError`.

Internal (used by ``create_admin`` only): :mod:`.lifecycle`.
"""

from __future__ import annotations

from asterion.extensions.base import AdminExtension
from asterion.extensions.context import ExtensionContext
from asterion.extensions.errors import (
    DuplicateExtensionError,
    ExtensionError,
    RegistryFrozenError,
)
from asterion.extensions.registry import ExtensionRegistry

__all__ = [
    "AdminExtension",
    "DuplicateExtensionError",
    "ExtensionContext",
    "ExtensionError",
    "ExtensionRegistry",
    "RegistryFrozenError",
]
