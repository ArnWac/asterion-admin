"""Field adapter protocol + neutral DTOs.

A :class:`FieldAdapter` decouples the framework from SQLAlchemy column
types. The :class:`~asterion.fields.registry.FieldRegistry` finds the
first adapter whose :meth:`supports` returns True for a given SQLAlchemy
column (or, later, a non-column attribute like a hybrid property), then
delegates contract-building, serialization, and parsing to it.

DTOs in this module never import SQLAlchemy. Adapters that need
SQLAlchemy types live in :mod:`asterion.fields.scalar` (and later
``relation``, ``files``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class FieldContract:
    """Renderer-neutral description of a single admin field.

    Mirrors the public shape that ``contract/service.py`` returns today
    (``FieldMeta``) so the registry can replace the inline introspection
    without changing the wire format. Block A4 will add ``widget``,
    ``required``, ``help_text``, etc. — those are intentionally absent
    here so A1 stays additive.
    """

    name: str
    type: str
    primary_key: bool = False
    read_only: bool = False
    hidden: bool = False
    nullable: bool = False
    calculated: bool = False
    python_type: type | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class FieldAdapter(Protocol):
    """Turns a model attribute into a :class:`FieldContract`.

    Adapters are stateless. ``supports`` is the cheap discriminator the
    registry uses to pick an adapter; ``build_contract`` produces the
    contract entry; ``serialize`` and ``parse`` are the optional hooks
    later phases will use to customize wire-format coercion.

    The ``ctx`` argument is forward-looking — A3 will start passing the
    AdminContext through so adapters can branch on tenant / role; today
    callers pass ``None`` and adapters ignore it.
    """

    name: str

    def supports(self, model_attr: Any) -> bool: ...

    def build_contract(self, model_attr: Any, ctx: Any | None = None) -> FieldContract: ...

    def serialize(self, value: Any, ctx: Any | None = None) -> Any: ...

    def parse(self, value: Any, ctx: Any | None = None) -> Any: ...
