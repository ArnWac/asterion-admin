"""A6 contract extensions: form layout via :class:`Fieldset`.

Covers:
* Basic Fieldset declaration on ModelAdmin → emitted as FieldsetMeta.
* Collapsed flag + description propagation.
* Unknown field names dropped (graceful degradation).
* Protected fields dropped.
* Calculated fields are allowed in fieldsets.
* Empty fieldsets (no declaration) → empty list in contract.
* Per-subclass isolation (one admin's fieldsets must not leak into
  another).
"""

from __future__ import annotations

from sqlalchemy import Column, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase

from asterion.admin import Fieldset
from asterion.contract.service import (
    build_fieldset_metadata,
    build_model_contract,
)
from asterion.registry import ModelAdmin


class _Base(DeclarativeBase):
    pass


class _Article(_Base):
    __tablename__ = "a6_articles"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    slug = Column(String(200), nullable=False)
    body = Column(Text, nullable=False)
    seo_title = Column(String(200), nullable=True)
    seo_description = Column(String(500), nullable=True)
    secret_token = Column(String(64), nullable=True)


class _ArticleAdmin(ModelAdmin):
    model = _Article
    protected_fields = ["secret_token"]
    calculated_fields = {"display_name": lambda obj: obj.title}

    fieldsets = [
        Fieldset("Content", fields=["title", "slug", "body"]),
        Fieldset(
            "SEO",
            fields=["seo_title", "seo_description"],
            collapsed=True,
            description="Search engine metadata.",
        ),
        Fieldset("Computed", fields=["display_name"]),
    ]


# ---------------------------------------------------------------------------
# Basic shape
# ---------------------------------------------------------------------------


def test_fieldset_metadata_emitted_in_declaration_order():
    metas = build_fieldset_metadata(_ArticleAdmin())
    labels = [m.label for m in metas]
    assert labels == ["Content", "SEO", "Computed"]


def test_fieldset_collapsed_and_description_propagate():
    metas = build_fieldset_metadata(_ArticleAdmin())
    seo = next(m for m in metas if m.label == "SEO")
    assert seo.collapsed is True
    assert seo.description == "Search engine metadata."


def test_fieldset_fields_match_declaration():
    metas = build_fieldset_metadata(_ArticleAdmin())
    content = next(m for m in metas if m.label == "Content")
    assert content.fields == ["title", "slug", "body"]


def test_calculated_fields_accepted_in_fieldsets():
    """Calculated fields are virtual but renderable — listing them in
    a fieldset must work."""
    metas = build_fieldset_metadata(_ArticleAdmin())
    computed = next(m for m in metas if m.label == "Computed")
    assert computed.fields == ["display_name"]


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


class _AdminWithUnknown(ModelAdmin):
    model = _Article
    fieldsets = [
        Fieldset("Mixed", fields=["title", "does_not_exist", "slug"]),
    ]


def test_unknown_fields_dropped_silently():
    """A field name that isn't a column or calculated field is silently
    dropped — declaring order for the remaining entries is preserved."""
    metas = build_fieldset_metadata(_AdminWithUnknown())
    assert metas[0].fields == ["title", "slug"]


class _AdminLeakingSecret(ModelAdmin):
    model = _Article
    protected_fields = ["secret_token"]
    fieldsets = [
        Fieldset("Bad", fields=["title", "secret_token"]),
    ]


def test_protected_field_dropped_from_fieldset():
    """Listing a protected field in a fieldset must not bypass the
    protection — the field is filtered out and the section renders
    without it."""
    metas = build_fieldset_metadata(_AdminLeakingSecret())
    assert "secret_token" not in metas[0].fields
    assert metas[0].fields == ["title"]


class _AdminDuplicateField(ModelAdmin):
    model = _Article
    fieldsets = [Fieldset("Dup", fields=["title", "title", "slug"])]


def test_duplicate_field_inside_fieldset_deduplicated():
    """Two entries for the same field inside one fieldset would mount
    the widget twice — deduplicate while preserving first occurrence."""
    metas = build_fieldset_metadata(_AdminDuplicateField())
    assert metas[0].fields == ["title", "slug"]


# ---------------------------------------------------------------------------
# Empty / per-subclass isolation
# ---------------------------------------------------------------------------


class _AdminNoFieldsets(ModelAdmin):
    model = _Article


def test_no_fieldsets_returns_empty_list():
    metas = build_fieldset_metadata(_AdminNoFieldsets())
    assert metas == []


def test_per_subclass_default_isolation():
    """``__init_subclass__`` must give each subclass its own empty
    ``fieldsets`` list, so an admin without a declaration never shares
    another admin's list."""
    assert _AdminNoFieldsets.fieldsets == []
    # _ArticleAdmin sets its own list — leave it alone.
    assert len(_ArticleAdmin.fieldsets) == 3
    # And the bare base class still has an empty default.
    assert ModelAdmin.fieldsets == []


# ---------------------------------------------------------------------------
# Full contract integration
# ---------------------------------------------------------------------------


def test_build_model_contract_emits_fieldsets():
    contract = build_model_contract(_ArticleAdmin())
    labels = [fs.label for fs in contract.fieldsets]
    assert labels == ["Content", "SEO", "Computed"]


def test_build_model_contract_without_fieldsets_emits_empty_list():
    contract = build_model_contract(_AdminNoFieldsets())
    assert contract.fieldsets == []
