"""A5 contract extensions: relations introspected from mapper.relationships.

Covers:
* belongs_to (MANYTOONE) — typical FK column on the source.
* has_many (ONETOMANY) — collection on the parent.
* many_to_many (MANYTOMANY) — assoc table exposed via ``secondary``.
* self-reference — a model that points at itself.
* ``target_registered`` flag — driven by the AdminRegistry.
"""

from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Integer, String, Table
from sqlalchemy.orm import DeclarativeBase, relationship

from asterion.contract.service import (
    build_model_contract,
    build_relation_metadata,
)
from asterion.registry import AdminRegistry, ModelAdmin


class _Base(DeclarativeBase):
    pass


class _Tenant(_Base):
    __tablename__ = "a5_tenants"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    posts = relationship("_Post", back_populates="tenant")


_post_tags = Table(
    "a5_post_tags",
    _Base.metadata,
    Column("post_id", Integer, ForeignKey("a5_posts.id"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("a5_tags.id"), primary_key=True),
)


class _Post(_Base):
    __tablename__ = "a5_posts"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    tenant_id = Column(Integer, ForeignKey("a5_tenants.id"), nullable=False)
    parent_id = Column(Integer, ForeignKey("a5_posts.id"), nullable=True)

    tenant = relationship("_Tenant", back_populates="posts")
    parent = relationship("_Post", remote_side="_Post.id")
    tags = relationship("_Tag", secondary=_post_tags, back_populates="posts")


class _Tag(_Base):
    __tablename__ = "a5_tags"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    posts = relationship("_Post", secondary=_post_tags, back_populates="tags")


class _TenantAdmin(ModelAdmin):
    model = _Tenant


class _PostAdmin(ModelAdmin):
    model = _Post


class _TagAdmin(ModelAdmin):
    model = _Tag


def _by_name(relations, name):
    return next(r for r in relations if r.name == name)


# ---------------------------------------------------------------------------
# Direction mapping
# ---------------------------------------------------------------------------


def test_belongs_to_relation_emitted_for_many_to_one():
    relations = build_relation_metadata(_PostAdmin())
    rel = _by_name(relations, "tenant")
    assert rel.kind == "belongs_to"
    assert rel.target == "a5_tenants"
    # FK lives on the source side for MANYTOONE.
    assert rel.local_columns == ["tenant_id"]
    assert rel.remote_columns == ["id"]
    assert rel.secondary is None


def test_has_many_relation_emitted_for_one_to_many():
    relations = build_relation_metadata(_TenantAdmin())
    rel = _by_name(relations, "posts")
    assert rel.kind == "has_many"
    assert rel.target == "a5_posts"
    # Tenant.id (local PK) maps to Post.tenant_id (remote FK).
    assert rel.local_columns == ["id"]
    assert rel.remote_columns == ["tenant_id"]
    assert rel.secondary is None


def test_many_to_many_relation_exposes_assoc_table():
    relations = build_relation_metadata(_PostAdmin())
    rel = _by_name(relations, "tags")
    assert rel.kind == "many_to_many"
    assert rel.target == "a5_tags"
    assert rel.secondary == "a5_post_tags"


# ---------------------------------------------------------------------------
# Self-reference
# ---------------------------------------------------------------------------


def test_self_referencing_relation_handled():
    """A model pointing at itself: parent_id → Post.id. The kind stays
    ``belongs_to`` and the target equals the source table name."""
    relations = build_relation_metadata(_PostAdmin())
    rel = _by_name(relations, "parent")
    assert rel.kind == "belongs_to"
    assert rel.target == "a5_posts"
    assert rel.local_columns == ["parent_id"]


# ---------------------------------------------------------------------------
# target_registered flag
# ---------------------------------------------------------------------------


def test_target_registered_false_without_admin_registry():
    """No admin_registry → cannot know what's registered → False."""
    relations = build_relation_metadata(_PostAdmin())
    for rel in relations:
        assert rel.target_registered is False


def test_target_registered_true_when_target_in_registry():
    """Pass an AdminRegistry that knows about ``a5_tenants`` and
    ``a5_tags``. Those relations get ``target_registered=True``; the
    self-reference also flips True (target == own table, which is
    registered as _PostAdmin)."""
    reg = AdminRegistry()
    reg.register(_PostAdmin())
    reg.register(_TenantAdmin())
    reg.register(_TagAdmin())

    relations = build_relation_metadata(_PostAdmin(), admin_registry=reg)
    assert _by_name(relations, "tenant").target_registered is True
    assert _by_name(relations, "tags").target_registered is True
    assert _by_name(relations, "parent").target_registered is True


def test_target_registered_false_for_unregistered_target():
    """Registry knows _PostAdmin but not _TenantAdmin → tenant relation
    has target_registered=False (correctly), self-ref still True."""
    reg = AdminRegistry()
    reg.register(_PostAdmin())

    relations = build_relation_metadata(_PostAdmin(), admin_registry=reg)
    assert _by_name(relations, "tenant").target_registered is False
    assert _by_name(relations, "parent").target_registered is True


# ---------------------------------------------------------------------------
# Empty case
# ---------------------------------------------------------------------------


class _Standalone(_Base):
    __tablename__ = "a5_standalone"
    id = Column(Integer, primary_key=True)


class _StandaloneAdmin(ModelAdmin):
    model = _Standalone


def test_no_relations_returns_empty_list():
    """A model with zero relationships emits ``relations=[]`` — important
    so clients don't have to guard against ``None``."""
    relations = build_relation_metadata(_StandaloneAdmin())
    assert relations == []


# ---------------------------------------------------------------------------
# Full contract integration
# ---------------------------------------------------------------------------


def test_build_model_contract_includes_relations():
    contract = build_model_contract(_PostAdmin())
    names = sorted(r.name for r in contract.relations)
    assert names == ["parent", "tags", "tenant"]


def test_build_model_contract_relations_use_admin_registry():
    """The router passes ``admin_registry=runtime.registry`` through;
    confirm the same plumbing on the function level."""
    reg = AdminRegistry()
    reg.register(_PostAdmin())
    reg.register(_TenantAdmin())
    contract = build_model_contract(_PostAdmin(), admin_registry=reg)
    rel = _by_name(contract.relations, "tenant")
    assert rel.target_registered is True
