"""C1 contract extensions: ``InlineAdmin`` declaration + wire format.

Covers the static surface and the contract output. Transactional
parent/child CRUD plumbing is verified in C2.
"""

from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase

from asterion.admin import InlineAdmin
from asterion.contract.service import (
    build_inline_metadata,
    build_model_contract,
)
from asterion.registry import ModelAdmin


class _Base(DeclarativeBase):
    pass


class _Post(_Base):
    __tablename__ = "c1_posts"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)


class _Comment(_Base):
    __tablename__ = "c1_comments"
    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey("c1_posts.id"), nullable=False)
    author = Column(String(100), nullable=False)
    body = Column(String(1000), nullable=False)
    created_at = Column(String(40), nullable=True)


class _CommentInline(InlineAdmin):
    model = _Comment
    fk_name = "post_id"
    fields = ["author", "body"]
    readonly_fields = ["created_at"]
    extra = 2
    max_num = 50
    can_delete = True
    ordering = ["created_at"]
    label = "Comments"


class _PostAdmin(ModelAdmin):
    model = _Post
    inlines = [_CommentInline]


# ---------------------------------------------------------------------------
# Basic shape
# ---------------------------------------------------------------------------


def test_inline_metadata_emitted():
    metas = build_inline_metadata(_PostAdmin())
    assert len(metas) == 1
    inline = metas[0]
    assert inline.model == "c1_comments"
    assert inline.fk_name == "post_id"
    assert inline.label == "Comments"


def test_inline_field_order_preserved():
    inline = build_inline_metadata(_PostAdmin())[0]
    assert inline.fields == ["author", "body"]


def test_inline_readonly_fields_propagate():
    inline = build_inline_metadata(_PostAdmin())[0]
    assert inline.readonly_fields == ["created_at"]


def test_inline_extra_max_num_can_delete_propagate():
    inline = build_inline_metadata(_PostAdmin())[0]
    assert inline.extra == 2
    assert inline.max_num == 50
    assert inline.can_delete is True


def test_inline_ordering_propagates():
    inline = build_inline_metadata(_PostAdmin())[0]
    assert inline.ordering == ["created_at"]


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


class _BareInline(InlineAdmin):
    """No ``fields`` declared → default to all columns except fk."""

    model = _Comment
    fk_name = "post_id"


class _BareAdmin(ModelAdmin):
    model = _Post
    inlines = [_BareInline]


def test_empty_fields_defaults_to_all_columns_except_fk():
    inline = build_inline_metadata(_BareAdmin())[0]
    # Should include id + author + body + created_at, NOT post_id (the fk).
    assert "post_id" not in inline.fields
    assert set(inline.fields) >= {"author", "body", "created_at"}


def test_no_label_falls_back_to_class_name():
    inline = build_inline_metadata(_BareAdmin())[0]
    assert inline.label == "_Comment"


def test_default_can_delete_is_true():
    inline = build_inline_metadata(_BareAdmin())[0]
    assert inline.can_delete is True


def test_default_extra_is_zero():
    inline = build_inline_metadata(_BareAdmin())[0]
    assert inline.extra == 0


def test_default_max_num_is_none():
    inline = build_inline_metadata(_BareAdmin())[0]
    assert inline.max_num is None


# ---------------------------------------------------------------------------
# Class vs. instance both accepted
# ---------------------------------------------------------------------------


class _ClassAndInstanceAdmin(ModelAdmin):
    model = _Post
    inlines = [_CommentInline, _BareInline()]  # one class, one instance


def test_inlines_accept_class_or_instance():
    metas = build_inline_metadata(_ClassAndInstanceAdmin())
    assert len(metas) == 2


# ---------------------------------------------------------------------------
# Per-subclass isolation
# ---------------------------------------------------------------------------


class _AdminWithoutInlines(ModelAdmin):
    model = _Post


def test_admins_without_inlines_emit_empty_list():
    assert _AdminWithoutInlines().inlines == []
    assert build_inline_metadata(_AdminWithoutInlines()) == []


def test_per_subclass_default_isolation():
    """``__init_subclass__`` must give each subclass its own empty
    ``inlines`` list."""
    assert _PostAdmin.inlines != []
    assert _AdminWithoutInlines.inlines == []
    assert ModelAdmin.inlines == []


# ---------------------------------------------------------------------------
# Full contract integration
# ---------------------------------------------------------------------------


def test_build_model_contract_includes_inlines():
    contract = build_model_contract(_PostAdmin())
    assert len(contract.inlines) == 1
    assert contract.inlines[0].model == "c1_comments"


def test_build_model_contract_empty_inlines_default():
    contract = build_model_contract(_AdminWithoutInlines())
    assert contract.inlines == []
