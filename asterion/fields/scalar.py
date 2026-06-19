"""Default scalar field adapters covering the v1 SQLAlchemy column types.

These six adapters reproduce the lookup logic currently inlined in
:func:`asterion.contract.service._field_type` and
:func:`asterion.schemas.builder._sa_type_to_python`. Phase A3 will
delete those inline switches and route through this module.

Registration order in the default registry matters:

    UUID -> Boolean -> DateTime -> Float -> Integer -> String

UUID first because some UUID dialects render as ``CHAR(32)``; Boolean
before Integer because some backends store it as a tinyint that
``isinstance(Integer)`` would still match; Float before Integer for the
same reason on the Numeric side; String last as the universal fallback.

Each adapter accepts a SQLAlchemy :class:`Column`. ``serialize`` and
``parse`` are identity by default — A2 will override them for Enum,
JSON, Secret, etc.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy.types as sqltypes
from sqlalchemy import Column

from asterion.fields.base import FieldContract


def _base_contract(col: Column, type_name: str, py_type: type) -> FieldContract:
    """Common shape every scalar adapter returns."""
    return FieldContract(
        name=col.name,
        type=type_name,
        primary_key=bool(col.primary_key),
        read_only=bool(col.primary_key),
        hidden=False,
        nullable=bool(col.nullable),
        calculated=False,
        python_type=py_type,
    )


class UUIDAdapter:
    """SQLAlchemy UUID/GUID columns.

    Detection is by type-class name (``"uuid"`` / ``"guid"`` substring)
    because the UUID type lives in different namespaces across SA
    versions and dialects, and exists as a real ``Uuid`` class only in
    SA 2.0+. Substring lookup is robust to both.

    ``serialize`` converts :class:`uuid.UUID` values to their canonical
    string form for the wire — pre-1.3 this lived in
    ``serializer._serialize_value``; consolidated here so column-type
    coercion has one source of truth.
    """

    name = "uuid"

    def supports(self, model_attr: Any) -> bool:
        if not isinstance(model_attr, Column):
            return False
        type_name = type(model_attr.type).__name__.lower()
        return "uuid" in type_name or "guid" in type_name

    def build_contract(self, model_attr: Column, ctx: Any | None = None) -> FieldContract:
        return _base_contract(model_attr, "uuid", uuid.UUID)

    def serialize(self, value: Any, ctx: Any | None = None) -> Any:
        if isinstance(value, uuid.UUID):
            return str(value)
        return value

    def parse(self, value: Any, ctx: Any | None = None) -> Any:
        return value


class BooleanAdapter:
    name = "boolean"

    def supports(self, model_attr: Any) -> bool:
        return isinstance(model_attr, Column) and isinstance(model_attr.type, sqltypes.Boolean)

    def build_contract(self, model_attr: Column, ctx: Any | None = None) -> FieldContract:
        return _base_contract(model_attr, "boolean", bool)

    def serialize(self, value: Any, ctx: Any | None = None) -> Any:
        return value

    def parse(self, value: Any, ctx: Any | None = None) -> Any:
        return value


class DateTimeAdapter:
    """SQLAlchemy ``DateTime`` columns.

    ``serialize`` returns ISO-8601 strings (``.isoformat()``) for the
    wire — pre-1.3 this lived in ``serializer._serialize_value``;
    consolidated here so column-type coercion has one source of truth.
    """

    name = "datetime"

    def supports(self, model_attr: Any) -> bool:
        return isinstance(model_attr, Column) and isinstance(model_attr.type, sqltypes.DateTime)

    def build_contract(self, model_attr: Column, ctx: Any | None = None) -> FieldContract:
        return _base_contract(model_attr, "datetime", datetime)

    def serialize(self, value: Any, ctx: Any | None = None) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    def parse(self, value: Any, ctx: Any | None = None) -> Any:
        return value


class FloatAdapter:
    """``Float`` and ``Numeric`` columns. ``Float`` is a subclass of
    ``Numeric`` in SQLAlchemy, so the single ``Numeric`` check covers
    both, but we keep the explicit name for clarity."""

    name = "float"

    def supports(self, model_attr: Any) -> bool:
        return isinstance(model_attr, Column) and isinstance(
            model_attr.type, (sqltypes.Float, sqltypes.Numeric)
        )

    def build_contract(self, model_attr: Column, ctx: Any | None = None) -> FieldContract:
        return _base_contract(model_attr, "float", float)

    def serialize(self, value: Any, ctx: Any | None = None) -> Any:
        return value

    def parse(self, value: Any, ctx: Any | None = None) -> Any:
        return value


class IntegerAdapter:
    """``Integer`` and its subclasses (``BigInteger``, ``SmallInteger``)."""

    name = "integer"

    def supports(self, model_attr: Any) -> bool:
        return isinstance(model_attr, Column) and isinstance(model_attr.type, sqltypes.Integer)

    def build_contract(self, model_attr: Column, ctx: Any | None = None) -> FieldContract:
        return _base_contract(model_attr, "integer", int)

    def serialize(self, value: Any, ctx: Any | None = None) -> Any:
        return value

    def parse(self, value: Any, ctx: Any | None = None) -> Any:
        return value


class EnumAdapter:
    """SQLAlchemy ``Enum`` columns.

    Wire-format ``type`` stays ``"string"`` for A2 (no contract bump);
    the discriminating information sits in ``metadata["choices"]`` and
    ``metadata["widget"] = "select"``. A4 will promote ``"enum"`` to a
    first-class contract type.

    Must be registered **before** ``StringAdapter`` because SA's
    ``Enum`` subclasses ``String`` — the catch-all would otherwise win.
    """

    name = "enum"

    def supports(self, model_attr: Any) -> bool:
        return isinstance(model_attr, Column) and isinstance(model_attr.type, sqltypes.Enum)

    def build_contract(self, model_attr: Column, ctx: Any | None = None) -> FieldContract:
        sa_type = model_attr.type
        choices = list(getattr(sa_type, "enums", []) or [])
        contract = _base_contract(model_attr, "string", str)
        return FieldContract(
            name=contract.name,
            type=contract.type,
            primary_key=contract.primary_key,
            read_only=contract.read_only,
            hidden=contract.hidden,
            nullable=contract.nullable,
            calculated=contract.calculated,
            python_type=contract.python_type,
            metadata={"widget": "select", "choices": choices},
        )

    def serialize(self, value: Any, ctx: Any | None = None) -> Any:
        return value

    def parse(self, value: Any, ctx: Any | None = None) -> Any:
        return value


class JSONAdapter:
    """SQLAlchemy ``JSON`` columns.

    Wire-format ``type`` stays ``"string"`` for A2 compatibility; A4
    will promote to ``"json"`` once the contract version bumps. UI
    renderer hint is exposed via ``metadata["widget"] = "json"``.
    """

    name = "json"

    def supports(self, model_attr: Any) -> bool:
        return isinstance(model_attr, Column) and isinstance(model_attr.type, sqltypes.JSON)

    def build_contract(self, model_attr: Column, ctx: Any | None = None) -> FieldContract:
        contract = _base_contract(model_attr, "string", str)
        return FieldContract(
            name=contract.name,
            type=contract.type,
            primary_key=contract.primary_key,
            read_only=contract.read_only,
            hidden=contract.hidden,
            nullable=contract.nullable,
            calculated=contract.calculated,
            python_type=dict,
            metadata={"widget": "json"},
        )

    def serialize(self, value: Any, ctx: Any | None = None) -> Any:
        return value

    def parse(self, value: Any, ctx: Any | None = None) -> Any:
        return value


class TextAdapter:
    """SQLAlchemy ``Text`` columns (long-form text → ``<textarea>``).

    Wire-format ``type`` stays ``"string"`` (Text is a String subclass
    today). UI hint via ``metadata["widget"] = "textarea"``. Must be
    registered **before** ``StringAdapter`` because Text isinstance
    String.
    """

    name = "text"

    def supports(self, model_attr: Any) -> bool:
        return (
            isinstance(model_attr, Column)
            and isinstance(model_attr.type, sqltypes.Text)
            and not isinstance(model_attr.type, sqltypes.Enum)
        )

    def build_contract(self, model_attr: Column, ctx: Any | None = None) -> FieldContract:
        contract = _base_contract(model_attr, "string", str)
        metadata: dict[str, Any] = {"widget": "textarea"}
        # Some dialects accept TEXT(N) — propagate the cap when present
        # so the textarea can enforce it client-side.
        length = getattr(model_attr.type, "length", None)
        if isinstance(length, int) and length > 0:
            metadata["max_length"] = length
        return FieldContract(
            name=contract.name,
            type=contract.type,
            primary_key=contract.primary_key,
            read_only=contract.read_only,
            hidden=contract.hidden,
            nullable=contract.nullable,
            calculated=contract.calculated,
            python_type=contract.python_type,
            metadata=metadata,
        )

    def serialize(self, value: Any, ctx: Any | None = None) -> Any:
        return value

    def parse(self, value: Any, ctx: Any | None = None) -> Any:
        return value


class StringAdapter:
    """``String`` and any other column type the earlier adapters did
    not claim. Universal fallback — must be registered last.

    ``supports`` returns True for any :class:`Column` so the registry
    never returns ``None`` for a real column. Non-column attributes
    (hybrid properties, calculated fields) are handled outside the
    registry by the caller.

    Validation hints (Roadmap 2.3): when the column declares a length
    (``Column(String(200))``), the adapter exposes ``max_length=200``
    in metadata. The contract builder's ``_split_widget_and_validation``
    promotes it into :data:`FieldMeta.validation` so the client form
    can size + validate the input without re-introspecting the model.
    """

    name = "string"

    def supports(self, model_attr: Any) -> bool:
        return isinstance(model_attr, Column)

    def build_contract(self, model_attr: Column, ctx: Any | None = None) -> FieldContract:
        contract = _base_contract(model_attr, "string", str)
        metadata: dict[str, Any] = {}
        length = getattr(model_attr.type, "length", None)
        if isinstance(length, int) and length > 0:
            metadata["max_length"] = length
        if not metadata:
            return contract
        return FieldContract(
            name=contract.name,
            type=contract.type,
            primary_key=contract.primary_key,
            read_only=contract.read_only,
            hidden=contract.hidden,
            nullable=contract.nullable,
            calculated=contract.calculated,
            python_type=contract.python_type,
            metadata=metadata,
        )

    def serialize(self, value: Any, ctx: Any | None = None) -> Any:
        return value

    def parse(self, value: Any, ctx: Any | None = None) -> Any:
        return value


DEFAULT_SCALAR_ADAPTERS: tuple[type, ...] = (
    UUIDAdapter,
    BooleanAdapter,
    DateTimeAdapter,
    FloatAdapter,
    IntegerAdapter,
    EnumAdapter,
    JSONAdapter,
    TextAdapter,
    StringAdapter,
)
