"""Tests for the field registry lookup mechanics.

These tests use small fake adapters to keep the registry behavior under
test isolated from the SQLAlchemy-aware scalar adapters in
``test_scalar.py``.
"""

from __future__ import annotations

from typing import Any

from asterion.fields import FieldRegistry, build_default_registry
from asterion.fields.base import FieldContract


class _FakeAdapter:
    """Minimal adapter shape — accepts any value matching ``marker``."""

    def __init__(self, name: str, marker: type) -> None:
        self.name = name
        self._marker = marker

    def supports(self, model_attr: Any) -> bool:
        return isinstance(model_attr, self._marker)

    def build_contract(self, model_attr: Any, ctx: Any | None = None) -> FieldContract:
        return FieldContract(name=str(model_attr), type=self.name)

    def serialize(self, value: Any, ctx: Any | None = None) -> Any:
        return value

    def parse(self, value: Any, ctx: Any | None = None) -> Any:
        return value


def test_empty_registry_returns_none():
    registry = FieldRegistry()
    assert registry.find_adapter("anything") is None
    assert len(registry) == 0


def test_register_appends_in_order():
    registry = FieldRegistry()
    a = _FakeAdapter("int", int)
    b = _FakeAdapter("str", str)
    registry.register(a)
    registry.register(b)
    assert registry.adapters() == [a, b]
    assert len(registry) == 2


def test_find_adapter_returns_first_match():
    """When two adapters could claim the same value, registration order
    decides — the first one wins. This is the contract for resolving
    ambiguity (e.g. a subclass-aware adapter registered before the
    fallback)."""
    registry = FieldRegistry()
    catch_all = _FakeAdapter("any", object)
    int_only = _FakeAdapter("int", int)
    registry.register(int_only)
    registry.register(catch_all)

    found = registry.find_adapter(42)
    assert found is int_only


def test_prepend_takes_priority_over_registered_adapters():
    registry = FieldRegistry()
    registry.register(_FakeAdapter("any", object))
    custom = _FakeAdapter("int", int)
    registry.prepend(custom)

    assert registry.find_adapter(7) is custom


def test_find_adapter_returns_none_when_no_supports():
    registry = FieldRegistry()
    registry.register(_FakeAdapter("int", int))
    assert registry.find_adapter("a string") is None


def test_adapters_returns_copy():
    """``adapters()`` returns a shallow copy so callers can't mutate
    the registry by accident."""
    registry = FieldRegistry()
    a = _FakeAdapter("int", int)
    registry.register(a)

    view = registry.adapters()
    view.clear()

    assert registry.adapters() == [a]


def test_default_registry_has_expected_adapters():
    """Default order pins: relations before scalars, scalars in the
    canonical priority order documented in
    :func:`build_default_registry`."""
    registry = build_default_registry()
    names = [a.name for a in registry.adapters()]
    assert names == [
        "foreign_key",
        "file",
        "uuid",
        "boolean",
        "datetime",
        "float",
        "integer",
        "enum",
        "json",
        "text",
        "string",
    ]
    assert len(registry) == 11


def test_build_default_registry_returns_fresh_instance():
    """Each call returns a fresh registry — mutations in one must not
    leak to the next caller. Critical for tests that prepend custom
    adapters and assume isolation."""
    r1 = build_default_registry()
    r2 = build_default_registry()
    assert r1 is not r2
    assert r1.adapters() is not r2.adapters()
