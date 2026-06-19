"""A3 integration: contract/service.py now consults the field registry.

These tests cover the path
``build_model_contract -> build_field_metadata -> FieldRegistry``.
They verify two things:

1. **Backward compat:** the six SQLAlchemy types the pre-A3 ``_field_type``
   switch handled still produce the same wire-format ``type`` string,
   and the schema builder still picks the right Python type. The legacy
   tests in ``tests/contract/test_service.py`` cover most of that; this
   module adds Enum/JSON/Text/ForeignKey, which pre-A3 silently mapped
   to ``"string"`` without metadata.

2. **New surface:** FieldMeta now carries a ``metadata`` dict that
   exposes adapter hints (``widget``, ``choices``, ``foreign_key``)
   without breaking older clients (default ``{}``).
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Column,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase

from asterion.contract.service import (
    build_field_metadata,
    build_model_contract,
)
from asterion.fields import FieldRegistry, build_default_registry
from asterion.registry import ModelAdmin


class _Base(DeclarativeBase):
    pass


class _Tenant(_Base):
    __tablename__ = "t3_tenants"
    id = Column(Integer, primary_key=True)


class _Article(_Base):
    __tablename__ = "t3_articles"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    body = Column(Text, nullable=True)
    status = Column(Enum("draft", "published", name="t3_status"), nullable=False)
    payload = Column(JSON, nullable=True)
    tenant_id = Column(Integer, ForeignKey("t3_tenants.id"), nullable=False)


class _ArticleAdmin(ModelAdmin):
    model = _Article
    list_display = ["id", "title", "status"]


def _meta_by_name(metas, name):
    return next(m for m in metas if m.name == name)


def test_default_registry_exposes_enum_choices_in_metadata():
    metas = build_field_metadata(_ArticleAdmin())
    status = _meta_by_name(metas, "status")
    # Wire-format type stays "string" in A3/A4 (Enum still string-shaped
    # on the wire; A5+ may introduce a dedicated ``"enum"`` type).
    assert status.type == "string"
    # A4 promotes ``widget`` to a top-level field.
    assert status.widget == "select"
    # Choices stay in ``metadata`` — they are not yet promoted.
    assert set(status.metadata["choices"]) == {"draft", "published"}


def test_default_registry_exposes_text_widget_hint():
    metas = build_field_metadata(_ArticleAdmin())
    body = _meta_by_name(metas, "body")
    assert body.type == "string"
    assert body.widget == "textarea"


def test_default_registry_exposes_json_widget_hint():
    metas = build_field_metadata(_ArticleAdmin())
    payload = _meta_by_name(metas, "payload")
    assert payload.type == "string"
    assert payload.widget == "json"


def test_default_registry_exposes_foreign_key_target():
    metas = build_field_metadata(_ArticleAdmin())
    fk = _meta_by_name(metas, "tenant_id")
    # FK column's wire-format type still reflects the underlying integer.
    assert fk.type == "integer"
    assert fk.widget == "foreign_key"
    assert fk.metadata["foreign_key"]["table"] == "t3_tenants"
    assert fk.metadata["foreign_key"]["column"] == "id"


def test_plain_string_field_has_empty_metadata():
    """A plain String column (no widget hints) keeps an empty metadata
    dict and ``widget=None`` — older clients that ignore ``metadata``
    don't see new keys."""
    metas = build_field_metadata(_ArticleAdmin())
    title = _meta_by_name(metas, "title")
    assert title.type == "string"
    assert title.widget is None
    assert title.metadata == {}


def test_build_model_contract_uses_provided_registry():
    """The router passes ``runtime.fields`` through ``build_model_contract``.
    Verify the path: a registry with no adapters falls through the
    ``_field_meta_from_adapter`` fallback for every column."""
    empty = FieldRegistry()
    contract = build_model_contract(_ArticleAdmin(), registry=empty)
    # Every column gets the fallback FieldMeta: type="string" with no
    # metadata, regardless of underlying type. Confirms the registry is
    # actually consulted and not bypassed somewhere.
    for fm in contract.fields:
        if fm.calculated:
            continue
        assert fm.metadata == {}
        # Without an adapter, the wire-format type degrades to "string".
        assert fm.type == "string"


def test_extension_supplied_adapter_propagates_to_contract():
    """Simulates what an extension does: prepend a custom adapter onto
    the registry, then build the contract through that registry. The
    custom widget hint shows up in FieldMeta.metadata. This is the path
    A4 / extensions will rely on."""
    registry = build_default_registry()

    class _UppercaseTitleAdapter:
        name = "uppercase_title"

        def supports(self, model_attr):
            return isinstance(model_attr, Column) and model_attr.name == "title"

        def build_contract(self, model_attr, ctx=None):
            from asterion.fields.base import FieldContract

            return FieldContract(
                name=model_attr.name,
                type="string",
                primary_key=False,
                read_only=False,
                hidden=False,
                nullable=bool(model_attr.nullable),
                calculated=False,
                python_type=str,
                metadata={"widget": "uppercase"},
            )

        def serialize(self, value, ctx=None):
            return value

        def parse(self, value, ctx=None):
            return value

    registry.prepend(_UppercaseTitleAdapter())

    metas = build_field_metadata(_ArticleAdmin(), registry=registry)
    title = _meta_by_name(metas, "title")
    assert title.widget == "uppercase"
