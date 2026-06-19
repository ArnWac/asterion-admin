"""Field adapter registry — the lookup that replaces ``_TYPE_MAP``.

A registry holds an ordered list of :class:`~asterion.fields.base.FieldAdapter`
instances. :meth:`find_adapter` walks them in registration order and
returns the first whose ``supports()`` returns True for the given model
attribute, or ``None`` if nothing matches.

Order matters: more specific adapters must be registered before their
fallbacks. ``StringAdapter`` is the universal fallback, so it goes last
in the default registry.

This module deliberately has no import-time side effects. The default
registry is built once in :mod:`asterion.fields.__init__` so tests
can construct their own isolated registries.
"""

from __future__ import annotations

from typing import Any

from asterion.fields.base import FieldAdapter


class FieldRegistry:
    """Ordered list of adapters with first-match lookup.

    Apps that want custom field types (Phase A2 will add a few; user
    code will add more) call :meth:`register` at startup. The framework
    holds one default-populated registry on the runtime; extensions can
    contribute adapters to it via the existing extension lifecycle
    (Block A wires that up later).
    """

    def __init__(self) -> None:
        self._adapters: list[FieldAdapter] = []

    def register(self, adapter: FieldAdapter) -> None:
        """Append an adapter. Later registrations have lower priority."""
        self._adapters.append(adapter)

    def prepend(self, adapter: FieldAdapter) -> None:
        """Register at the front — useful when a custom adapter must
        beat a built-in one (e.g. an app-specific ``MoneyAdapter`` that
        wraps Integer columns)."""
        self._adapters.insert(0, adapter)

    def find_adapter(self, model_attr: Any) -> FieldAdapter | None:
        for adapter in self._adapters:
            if adapter.supports(model_attr):
                return adapter
        return None

    def adapters(self) -> list[FieldAdapter]:
        """Return a shallow copy of the adapter list (test introspection)."""
        return list(self._adapters)

    def __len__(self) -> int:
        return len(self._adapters)
