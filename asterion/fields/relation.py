"""Relation-shaped field adapters.

For A2 this covers the simplest case: a SQLAlchemy column with one or
more :class:`ForeignKey` constraints. M2M (``HasManyInlineField``,
``ManyToManyField``) and ``relationship()`` mappings come in later
phases — they don't fit the Column-shaped ``supports()`` signature and
need access to the SA mapper, which the registry call sites can't
provide today.

The ``ForeignKeyAdapter`` keeps the wire-format ``type`` aligned with
the column's underlying scalar (uuid / integer / string) so the
existing contract consumers (UI, schema builder) don't see a breaking
change. The discriminating info — target table, target column — lives
in ``metadata["foreign_key"]``. A4 will promote this into a proper
``relations`` block on the contract.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy.types as sqltypes
from sqlalchemy import Column

from asterion.fields.base import FieldContract


def _underlying_scalar_type(col: Column) -> tuple[str, type]:
    """Pick the wire-format type string + python type from the column's
    underlying SQLAlchemy type. Same priority order as the scalar
    adapters: UUID → Boolean → DateTime → Float → Integer → String."""
    import uuid as _uuid
    from datetime import datetime as _datetime

    type_name = type(col.type).__name__.lower()
    if "uuid" in type_name or "guid" in type_name:
        return "uuid", _uuid.UUID
    if isinstance(col.type, sqltypes.Boolean):
        return "boolean", bool
    if isinstance(col.type, sqltypes.DateTime):
        return "datetime", _datetime
    if isinstance(col.type, (sqltypes.Float, sqltypes.Numeric)):
        return "float", float
    if isinstance(col.type, sqltypes.Integer):
        return "integer", int
    return "string", str


class ForeignKeyAdapter:
    """Claims any column that carries one or more ``ForeignKey``
    constraints.

    Must be registered **before** the scalar adapters in the default
    registry, because the underlying column type (Integer / UUID) would
    otherwise be claimed by ``IntegerAdapter`` / ``UUIDAdapter`` first.

    The contract's ``type`` stays the scalar wire-format string so the
    UI's input rendering still works. The FK target lands in
    ``metadata["foreign_key"]``:

        {
            "table": "tenants",
            "column": "id",
            "fullname": "tenants.id",
        }

    Multi-target FKs (a column carrying two ``ForeignKey`` constraints)
    are rare but legal; we expose the first one in the singular keys
    and the full list under ``metadata["foreign_keys"]``.
    """

    name = "foreign_key"

    def supports(self, model_attr: Any) -> bool:
        return isinstance(model_attr, Column) and bool(model_attr.foreign_keys)

    def build_contract(self, model_attr: Column, ctx: Any | None = None) -> FieldContract:
        type_name, py_type = _underlying_scalar_type(model_attr)

        fks = []
        for fk in model_attr.foreign_keys:
            target = fk.column
            fks.append(
                {
                    "table": target.table.name if target is not None else None,
                    "column": target.name if target is not None else None,
                    "fullname": fk.target_fullname,
                }
            )

        metadata: dict[str, Any] = {"widget": "foreign_key", "foreign_keys": fks}
        if fks:
            metadata["foreign_key"] = fks[0]

        return FieldContract(
            name=model_attr.name,
            type=type_name,
            primary_key=bool(model_attr.primary_key),
            read_only=bool(model_attr.primary_key),
            hidden=False,
            nullable=bool(model_attr.nullable),
            calculated=False,
            python_type=py_type,
            metadata=metadata,
        )

    def serialize(self, value: Any, ctx: Any | None = None) -> Any:
        return value

    def parse(self, value: Any, ctx: Any | None = None) -> Any:
        return value


DEFAULT_RELATION_ADAPTERS: tuple[type, ...] = (ForeignKeyAdapter,)
