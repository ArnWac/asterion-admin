"""Tests for the six default scalar field adapters.

Each adapter is checked for:
  * ``supports()`` returns True for its target SQLAlchemy column types
    (and False for foreign ones).
  * ``build_contract()`` returns a :class:`FieldContract` with the
    expected ``type`` string and ``python_type``.
  * Primary-key + nullable propagation from the column.

The default registry must keep producing the same type strings the
contract router uses today — those strings are part of the public
contract wire format.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy.types as sqltypes
from sqlalchemy import BigInteger, Boolean, Column, DateTime, Float, Integer, Numeric, String, Text

from asterion.fields import (
    BooleanAdapter,
    DateTimeAdapter,
    FloatAdapter,
    IntegerAdapter,
    StringAdapter,
    UUIDAdapter,
    build_default_registry,
)


class _FakeUuid(sqltypes.TypeEngine):
    """Stand-in for backend-specific UUID type whose class name contains 'UUID'.

    SA 1.x didn't ship a single ``Uuid`` class; we detect by class-name
    substring. This test class lets us exercise the path without
    depending on the SA 2.0 ``Uuid`` type or a Postgres-specific import.
    """


def test_uuid_adapter_matches_class_name_substring():
    col = Column("ext_id", _FakeUuid())
    adapter = UUIDAdapter()
    assert adapter.supports(col) is True

    contract = adapter.build_contract(col)
    assert contract.type == "uuid"
    assert contract.python_type is uuid.UUID
    assert contract.name == "ext_id"


def test_uuid_adapter_rejects_non_column():
    assert UUIDAdapter().supports("not a column") is False
    assert UUIDAdapter().supports(None) is False


def test_boolean_adapter():
    col = Column("active", Boolean(), nullable=False)
    adapter = BooleanAdapter()
    assert adapter.supports(col) is True
    contract = adapter.build_contract(col)
    assert contract.type == "boolean"
    assert contract.python_type is bool
    assert contract.nullable is False


def test_boolean_adapter_rejects_integer():
    col = Column("count", Integer())
    assert BooleanAdapter().supports(col) is False


def test_datetime_adapter():
    col = Column("created_at", DateTime(), nullable=True)
    adapter = DateTimeAdapter()
    assert adapter.supports(col) is True
    contract = adapter.build_contract(col)
    assert contract.type == "datetime"
    assert contract.python_type is datetime
    assert contract.nullable is True


def test_float_adapter_handles_float_and_numeric():
    """``Float`` and ``Numeric`` are both money-ish numeric columns —
    one adapter covers both. ``Float`` is a subclass of ``Numeric`` in
    SQLAlchemy."""
    adapter = FloatAdapter()
    assert adapter.supports(Column("a", Float())) is True
    assert adapter.supports(Column("b", Numeric())) is True

    contract = adapter.build_contract(Column("price", Numeric(10, 2)))
    assert contract.type == "float"
    assert contract.python_type is float


def test_integer_adapter_covers_subclasses():
    """BigInteger and SmallInteger are subclasses of Integer — one
    adapter covers all three."""
    adapter = IntegerAdapter()
    assert adapter.supports(Column("a", Integer())) is True
    assert adapter.supports(Column("b", BigInteger())) is True

    contract = adapter.build_contract(Column("count", Integer(), primary_key=True))
    assert contract.type == "integer"
    assert contract.python_type is int
    assert contract.primary_key is True
    assert contract.read_only is True


def test_string_adapter_handles_string_and_text():
    adapter = StringAdapter()
    assert adapter.supports(Column("title", String(200))) is True
    assert adapter.supports(Column("body", Text())) is True

    contract = adapter.build_contract(Column("title", String(200), nullable=False))
    assert contract.type == "string"
    assert contract.python_type is str
    assert contract.nullable is False


def test_string_adapter_is_universal_fallback():
    """The string adapter is the registered last-resort — it claims any
    Column, regardless of its inner type. This is the contract that
    keeps ``find_adapter`` from ever returning ``None`` for a real
    column."""
    adapter = StringAdapter()
    assert adapter.supports(Column("a", Float())) is True
    assert adapter.supports(Column("b", Boolean())) is True


def test_default_registry_picks_uuid_before_string():
    """Registration order in build_default_registry must keep UUID
    before the catch-all String. If someone reorders the tuple by
    accident, this test breaks."""
    registry = build_default_registry()
    col = Column("id", _FakeUuid(), primary_key=True)
    adapter = registry.find_adapter(col)
    assert adapter is not None
    assert adapter.name == "uuid"


def test_default_registry_picks_boolean_before_integer():
    registry = build_default_registry()
    col = Column("is_active", Boolean())
    adapter = registry.find_adapter(col)
    assert adapter is not None
    assert adapter.name == "boolean"


def test_default_registry_falls_back_to_string():
    """A plain String column must hit StringAdapter (the catch-all).
    Text and Enum, which both inherit from String, are handled by
    their dedicated adapters — see test_extra_adapters.py for those.
    """
    registry = build_default_registry()
    col = Column("title", String(200))
    adapter = registry.find_adapter(col)
    assert adapter is not None
    assert adapter.name == "string"


def test_default_registry_picks_float_for_numeric_money_columns():
    registry = build_default_registry()
    col = Column("price", Numeric(10, 2))
    adapter = registry.find_adapter(col)
    assert adapter is not None
    assert adapter.name == "float"


def test_parse_is_identity_for_default_scalars():
    """parse() remains identity for every default scalar adapter —
    SQLAlchemy already coerces input on bind, so per-adapter parsing
    is reserved for future custom types (Enum/JSON validators)."""
    for adapter in (
        UUIDAdapter(),
        BooleanAdapter(),
        DateTimeAdapter(),
        FloatAdapter(),
        IntegerAdapter(),
        StringAdapter(),
    ):
        assert adapter.parse(42) == 42


def test_serialize_is_identity_for_non_typed_scalars():
    """Bool / Float / Integer / String stay identity on serialize —
    these are JSON-native primitives. UUID/DateTime have their own
    tests below pinning the coercion behaviour."""
    for adapter in (
        BooleanAdapter(),
        FloatAdapter(),
        IntegerAdapter(),
        StringAdapter(),
    ):
        assert adapter.serialize("x") == "x"
        assert adapter.serialize(7) == 7


def test_uuid_adapter_serialize_coerces_uuid_to_str():
    """1.3 (Robustness): UUID-to-string coercion moves into the
    adapter so the serializer can stay column-type-agnostic. Non-UUID
    values pass through unchanged for defensive cases."""
    import uuid as _uuid

    adapter = UUIDAdapter()
    sample = _uuid.UUID("12345678-1234-5678-1234-567812345678")
    assert adapter.serialize(sample) == "12345678-1234-5678-1234-567812345678"
    # Already a string? leave it alone.
    assert adapter.serialize("not-a-uuid") == "not-a-uuid"
    # None passes through (nullable columns).
    assert adapter.serialize(None) is None


def test_datetime_adapter_serialize_coerces_datetime_to_isoformat():
    """1.3 (Robustness): datetime-to-isoformat coercion moves into the
    adapter. Non-datetime values pass through."""
    from datetime import datetime as _dt

    adapter = DateTimeAdapter()
    sample = _dt(2026, 5, 27, 14, 30, 0)
    assert adapter.serialize(sample) == "2026-05-27T14:30:00"
    assert adapter.serialize("already-a-string") == "already-a-string"
    assert adapter.serialize(None) is None
