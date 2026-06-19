"""Field adapter layer — turns model attributes into admin fields.

Phase A1 introduced this module additively. A2 added Enum, JSON, Text,
and ForeignKey adapters. Bestehender Code in ``schemas/builder.py`` und
``contract/service.py`` ist davon unberührt; A3 leitet beide auf die
Registry um.

Public surface:

* :class:`FieldAdapter` — Protocol every adapter implements.
* :class:`FieldContract` — neutral DTO produced by ``build_contract``.
* :class:`FieldRegistry` — ordered list of adapters with first-match
  lookup.
* :func:`build_default_registry` — convenience factory that registers
  the built-in adapters in canonical priority order.

Default registration order (priority highest first):

    ForeignKey      ← relations beat scalar lookups
    UUID
    Boolean
    DateTime
    Float
    Integer
    Enum            ← subclass of String, must beat String
    JSON
    Text            ← subclass of String, must beat String
    String          ← universal fallback
"""

from __future__ import annotations

from asterion.fields.base import FieldAdapter, FieldContract
from asterion.fields.file_field import (
    DEFAULT_FILE_ADAPTERS,
    FileFieldAdapter,
    FileFieldType,
)
from asterion.fields.registry import FieldRegistry
from asterion.fields.relation import DEFAULT_RELATION_ADAPTERS, ForeignKeyAdapter
from asterion.fields.scalar import (
    DEFAULT_SCALAR_ADAPTERS,
    BooleanAdapter,
    DateTimeAdapter,
    EnumAdapter,
    FloatAdapter,
    IntegerAdapter,
    JSONAdapter,
    StringAdapter,
    TextAdapter,
    UUIDAdapter,
)


def build_default_registry() -> FieldRegistry:
    """Construct a registry populated with all default adapters.

    Order: relation adapters first (so a FK column wins over the
    scalar that matches its underlying type), then file adapters
    (must beat StringAdapter — :class:`FileFieldType` extends String),
    then scalar adapters in the order pinned in
    :data:`DEFAULT_SCALAR_ADAPTERS`.

    Each call returns a fresh registry — tests that mutate the registry
    should not call this once and share. The runtime builds one of these
    per app in :func:`asterion.create_admin`.
    """
    registry = FieldRegistry()
    for adapter_cls in DEFAULT_RELATION_ADAPTERS:
        registry.register(adapter_cls())
    for adapter_cls in DEFAULT_FILE_ADAPTERS:
        registry.register(adapter_cls())
    for adapter_cls in DEFAULT_SCALAR_ADAPTERS:
        registry.register(adapter_cls())
    return registry


__all__ = [
    "DEFAULT_FILE_ADAPTERS",
    "DEFAULT_RELATION_ADAPTERS",
    "DEFAULT_SCALAR_ADAPTERS",
    "BooleanAdapter",
    "DateTimeAdapter",
    "EnumAdapter",
    "FieldAdapter",
    "FieldContract",
    "FieldRegistry",
    "FileFieldAdapter",
    "FileFieldType",
    "FloatAdapter",
    "ForeignKeyAdapter",
    "IntegerAdapter",
    "JSONAdapter",
    "StringAdapter",
    "TextAdapter",
    "UUIDAdapter",
    "build_default_registry",
]
