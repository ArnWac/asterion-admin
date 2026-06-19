"""Tests for the A2 adapter additions: Enum, JSON, Text, ForeignKey.

Each adapter must:
  * be discriminated correctly by ``supports()`` (no false positives
    against the other built-in column types),
  * keep the wire-format ``type`` string backward-compatible (A2 must
    not break the contract; A4 will bump it),
  * expose discriminating info via ``metadata``.

Ordering tests pin the default-registry priority so a future reorder
gets caught.
"""

from __future__ import annotations

import enum

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
)
from sqlalchemy.orm import DeclarativeBase

from asterion.fields import (
    EnumAdapter,
    ForeignKeyAdapter,
    JSONAdapter,
    StringAdapter,
    TextAdapter,
    build_default_registry,
)

# ---------------------------------------------------------------------------
# Enum
# ---------------------------------------------------------------------------


class _Status(enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"


def test_enum_adapter_supports_native_enum():
    col = Column("status", Enum(_Status))
    assert EnumAdapter().supports(col) is True


def test_enum_adapter_supports_value_list_enum():
    """SA also accepts ``Enum("a", "b", name="...")`` without a Python
    class. The adapter must still recognize it."""
    col = Column("kind", Enum("a", "b", name="kind_enum"))
    assert EnumAdapter().supports(col) is True


def test_enum_adapter_rejects_plain_string():
    assert EnumAdapter().supports(Column("title", String(200))) is False


def test_enum_adapter_contract_has_choices_and_keeps_string_type():
    """A2 keeps wire-format ``type="string"`` for Enum (no contract
    bump). Choices land in metadata so A4 can promote them later."""
    col = Column("status", Enum("draft", "published", name="status_enum"))
    contract = EnumAdapter().build_contract(col)
    assert contract.type == "string"
    assert contract.metadata["widget"] == "select"
    assert contract.metadata["choices"] == ["draft", "published"]


def test_enum_adapter_python_enum_choices():
    col = Column("status", Enum(_Status))
    contract = EnumAdapter().build_contract(col)
    # SA stores the member names as the enum values.
    assert set(contract.metadata["choices"]) == {"DRAFT", "PUBLISHED"}


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def test_json_adapter_supports_json_column():
    assert JSONAdapter().supports(Column("payload", JSON())) is True


def test_json_adapter_rejects_string():
    assert JSONAdapter().supports(Column("title", String(200))) is False


def test_json_adapter_contract():
    contract = JSONAdapter().build_contract(Column("payload", JSON(), nullable=True))
    assert contract.type == "string"  # wire-format compat for A2
    assert contract.metadata["widget"] == "json"
    assert contract.python_type is dict
    assert contract.nullable is True


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


def test_text_adapter_supports_text_column():
    assert TextAdapter().supports(Column("body", Text())) is True


def test_text_adapter_rejects_plain_string():
    """``Text`` is the SQLAlchemy long-text type; ``String`` (with or
    without length) is the short variant. The adapter distinguishes
    them so the UI can pick ``<textarea>`` vs ``<input>``."""
    assert TextAdapter().supports(Column("title", String(200))) is False


def test_text_adapter_rejects_enum():
    """Enum and Text both inherit from String. Without the explicit
    Enum exclusion, TextAdapter would claim Enum columns and the UI
    would render a textarea instead of a select. Pin the behavior."""
    col = Column("status", Enum("draft", "published", name="s"))
    assert TextAdapter().supports(col) is False


def test_text_adapter_contract_keeps_string_type_and_sets_widget():
    contract = TextAdapter().build_contract(Column("body", Text()))
    assert contract.type == "string"
    assert contract.metadata["widget"] == "textarea"


# ---------------------------------------------------------------------------
# ForeignKey
# ---------------------------------------------------------------------------


class _RelBase(DeclarativeBase):
    pass


_tenants = Table(
    "tenants",
    _RelBase.metadata,
    Column("id", Integer, primary_key=True),
)


def test_foreign_key_adapter_supports_fk_column():
    col = Column("tenant_id", Integer, ForeignKey("tenants.id"))
    # Bind the column to a table so the ForeignKey resolves cleanly.
    Table("t1", _RelBase.metadata, col, extend_existing=True)
    assert ForeignKeyAdapter().supports(col) is True


def test_foreign_key_adapter_rejects_plain_integer():
    col = Column("count", Integer())
    assert ForeignKeyAdapter().supports(col) is False


def test_foreign_key_adapter_contract_keeps_underlying_scalar_type():
    """FK column's wire-format ``type`` must match the underlying
    column type (integer here) so existing serializers / schema
    builders see no change in A2. Discrimination happens through the
    ``metadata.foreign_key`` block."""
    col = Column("tenant_id", Integer, ForeignKey("tenants.id"))
    Table("t2", _RelBase.metadata, col, extend_existing=True)

    contract = ForeignKeyAdapter().build_contract(col)
    assert contract.type == "integer"
    assert contract.python_type is int
    assert contract.metadata["widget"] == "foreign_key"
    fk = contract.metadata["foreign_key"]
    assert fk["table"] == "tenants"
    assert fk["column"] == "id"


def test_foreign_key_adapter_exposes_all_foreign_keys():
    """Rare: a column with two ForeignKey constraints. The adapter
    must expose all of them via ``metadata["foreign_keys"]`` and pick
    the first for the singular ``foreign_key`` key."""
    Table(
        "other",
        _RelBase.metadata,
        Column("id", Integer, primary_key=True),
        extend_existing=True,
    )
    col = Column(
        "ref",
        Integer,
        ForeignKey("tenants.id"),
        ForeignKey("other.id"),
    )
    Table("t3", _RelBase.metadata, col, extend_existing=True)

    contract = ForeignKeyAdapter().build_contract(col)
    assert len(contract.metadata["foreign_keys"]) == 2


# ---------------------------------------------------------------------------
# Default registry priority
# ---------------------------------------------------------------------------


def test_default_registry_picks_enum_over_string():
    """Enum is a String subclass. Without correct ordering, the
    catch-all StringAdapter would claim Enum columns."""
    registry = build_default_registry()
    col = Column("status", Enum("a", "b", name="s"))
    adapter = registry.find_adapter(col)
    assert adapter is not None
    assert adapter.name == "enum"


def test_default_registry_picks_text_over_string():
    registry = build_default_registry()
    col = Column("body", Text())
    adapter = registry.find_adapter(col)
    assert adapter is not None
    assert adapter.name == "text"


def test_default_registry_picks_foreign_key_over_underlying_scalar():
    """The whole point of registering FK adapters before scalar ones:
    a ``tenant_id INTEGER REFERENCES tenants(id)`` must be claimed by
    ForeignKeyAdapter, not IntegerAdapter."""
    Table(
        "fk_target",
        _RelBase.metadata,
        Column("id", Integer, primary_key=True),
        extend_existing=True,
    )
    col = Column("parent_id", Integer, ForeignKey("fk_target.id"))
    Table("fk_holder", _RelBase.metadata, col, extend_existing=True)

    registry = build_default_registry()
    adapter = registry.find_adapter(col)
    assert adapter is not None
    assert adapter.name == "foreign_key"


def test_default_registry_picks_json_over_string():
    registry = build_default_registry()
    col = Column("payload", JSON())
    adapter = registry.find_adapter(col)
    assert adapter is not None
    assert adapter.name == "json"


def test_default_registry_unchanged_for_plain_string():
    """Sanity: plain String column still falls through to StringAdapter."""
    registry = build_default_registry()
    col = Column("title", String(200))
    adapter = registry.find_adapter(col)
    assert adapter is not None
    assert adapter.name == "string"
    assert isinstance(adapter, StringAdapter)


def test_default_registry_unchanged_for_plain_boolean():
    """Sanity: scalar shortcuts (Boolean/DateTime/Float) still hit
    their dedicated adapter, not the new ones."""
    registry = build_default_registry()
    cases = [
        (Column("flag", Boolean()), "boolean"),
        (Column("created_at", DateTime()), "datetime"),
        (Column("price", Float()), "float"),
    ]
    for col, expected in cases:
        adapter = registry.find_adapter(col)
        assert adapter is not None, f"no adapter for {expected!r}"
        assert adapter.name == expected
