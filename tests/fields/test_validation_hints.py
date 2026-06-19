"""Roadmap 2.3 — adapters populate FieldMeta.validation.

Until now ``FieldMeta.validation`` was always ``{}`` because no adapter
emitted hints. Strings now expose ``max_length`` derived from the
SQLAlchemy column definition, so the client form can size + validate
the input without a second round-trip.
"""

from __future__ import annotations

from sqlalchemy import Column, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase

from asterion.contract.service import build_field_metadata
from asterion.fields import StringAdapter, TextAdapter, build_default_registry
from asterion.registry import ModelAdmin


class _Base(DeclarativeBase):
    pass


class _Article(_Base):
    __tablename__ = "v23_articles"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    short_name = Column(String(40), nullable=True)
    body = Column(Text, nullable=False)
    unbounded = Column(String, nullable=True)  # no length declared


class _ArticleAdmin(ModelAdmin):
    model = _Article


def _meta(name: str):
    metas = build_field_metadata(_ArticleAdmin())
    return next(m for m in metas if m.name == name)


# ---------------------------------------------------------------------------
# StringAdapter
# ---------------------------------------------------------------------------


def test_string_adapter_emits_max_length_when_declared():
    """``Column(String(200))`` propagates as ``max_length=200`` so the
    client form can enforce the cap client-side."""
    adapter = StringAdapter()
    col = Column("title", String(200))
    contract = adapter.build_contract(col)
    assert contract.metadata == {"max_length": 200}


def test_string_adapter_omits_max_length_when_unbounded():
    """``Column(String)`` (no length) emits no validation hint —
    matches the pre-2.3 wire shape for unbounded strings."""
    adapter = StringAdapter()
    col = Column("free", String())
    contract = adapter.build_contract(col)
    assert contract.metadata == {}


# ---------------------------------------------------------------------------
# TextAdapter
# ---------------------------------------------------------------------------


def test_text_adapter_keeps_widget_and_no_max_length_by_default():
    """Plain ``Column(Text())`` has no length — only the widget hint
    survives."""
    adapter = TextAdapter()
    col = Column("body", Text())
    contract = adapter.build_contract(col)
    assert contract.metadata == {"widget": "textarea"}


def test_text_adapter_emits_max_length_when_dialect_supports():
    """Some dialects accept ``Text(N)`` — propagate the cap alongside
    the widget hint."""
    adapter = TextAdapter()
    col = Column("body", Text(2000))
    contract = adapter.build_contract(col)
    assert contract.metadata == {"widget": "textarea", "max_length": 2000}


# ---------------------------------------------------------------------------
# End-to-end through FieldMeta
# ---------------------------------------------------------------------------


def test_field_metadata_promotes_max_length_into_validation():
    """The contract builder's ``_split_widget_and_validation`` lifts
    ``max_length`` out of metadata into the dedicated ``validation``
    slot on FieldMeta. Clients don't have to look in two places."""
    title = _meta("title")
    assert title.validation == {"max_length": 200}
    # max_length doesn't survive in metadata once promoted — keeps the
    # wire shape minimal.
    assert "max_length" not in title.metadata


def test_field_metadata_validation_empty_for_unbounded_string():
    unbounded = _meta("unbounded")
    assert unbounded.validation == {}


def test_field_metadata_validation_for_short_string():
    """Distinct entry to ensure the length actually round-trips, not a
    constant ``200`` somewhere."""
    short = _meta("short_name")
    assert short.validation == {"max_length": 40}


def test_text_max_length_round_trips_through_contract():
    """End-to-end: declaring ``Column(Text(1000))`` on a model produces
    ``FieldMeta.validation = {"max_length": 1000}``."""

    class _LimitedText(_Base):
        __tablename__ = "v23_limited_text"
        id = Column(Integer, primary_key=True)
        memo = Column(Text(1000), nullable=False)

    class _Admin(ModelAdmin):
        model = _LimitedText

    metas = build_field_metadata(_Admin())
    memo = next(m for m in metas if m.name == "memo")
    assert memo.validation == {"max_length": 1000}
    assert memo.widget == "textarea"


# ---------------------------------------------------------------------------
# Other adapters stay quiet (smoke)
# ---------------------------------------------------------------------------


def test_integer_adapter_emits_no_validation_hints():
    """Integer/Float/Bool/etc. don't have a default-emitable hint yet —
    pinning the silence so a future adapter change is intentional."""
    metas = build_field_metadata(_ArticleAdmin())
    id_field = next(m for m in metas if m.name == "id")
    assert id_field.validation == {}


def test_default_registry_unaffected_by_validation_changes():
    """Adding ``max_length`` to StringAdapter must not change the
    registry's adapter ordering — sanity that the rest of the contract
    pipeline still sees the same routing."""
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
