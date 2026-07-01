"""Tests for the slim v1 contract service."""

from __future__ import annotations

from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import DeclarativeBase

from asterion.contract.service import (
    CRUD_ACTIONS,
    build_field_metadata,
    build_model_contract,
)
from asterion.registry import ModelAdmin


class _Base(DeclarativeBase):
    pass


class Article(_Base):
    __tablename__ = "articles"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    content = Column(String, nullable=True)
    password_hash = Column(String, nullable=True)


class ArticleAdmin(ModelAdmin):
    model = Article
    list_display = ["id", "title"]
    search_fields = ["title"]
    readonly_fields = ["id"]


def test_build_field_metadata_excludes_globally_protected():
    fields = build_field_metadata(ArticleAdmin())
    names = [f.name for f in fields]
    assert "password_hash" not in names


def test_build_field_metadata_includes_columns():
    fields = build_field_metadata(ArticleAdmin())
    names = [f.name for f in fields]
    assert "title" in names
    assert "content" in names


def test_primary_key_is_read_only():
    fields = build_field_metadata(ArticleAdmin())
    id_field = next(f for f in fields if f.name == "id")
    assert id_field.read_only is True
    assert id_field.primary_key is True


def test_nullable_marked_correctly():
    fields = build_field_metadata(ArticleAdmin())
    content_field = next(f for f in fields if f.name == "content")
    title_field = next(f for f in fields if f.name == "title")
    assert content_field.nullable is True
    assert title_field.nullable is False


def test_build_model_contract_shape():
    contract = build_model_contract(ArticleAdmin())
    assert contract.resource == "articles"
    assert contract.contract_version == "2"
    assert contract.label == "Article"
    assert contract.label_plural == "Articles"
    assert contract.crud_actions == list(CRUD_ACTIONS)
    assert contract.admin_actions == []


def test_contract_list_display():
    contract = build_model_contract(ArticleAdmin())
    assert "id" in contract.list_display
    assert "title" in contract.list_display


def test_admin_action_meta_carries_icon_bulk_and_input_schema():
    """Theme D: the contract surfaces an action's ``icon`` / ``bulk`` /
    ``confirm`` / ``input_schema`` so the list view can render a per-row
    icon button with a typed-input dialog without a second round-trip."""
    from pydantic import BaseModel

    from asterion.actions import AdminAction

    class _Reason(BaseModel):
        reason: str

    class _Correct(AdminAction):
        name = "correct"
        label = "Correct entry"
        bulk = False
        confirm = True
        icon = "pencil"
        input_schema = _Reason

    class _Admin(ModelAdmin):
        model = Article
        actions = [_Correct()]

    meta = build_model_contract(_Admin()).admin_actions
    assert len(meta) == 1
    action = meta[0]
    assert action.name == "correct"
    assert action.bulk is False
    assert action.confirm is True
    assert action.icon == "pencil"
    assert action.input_schema is not None
    assert "reason" in action.input_schema.get("properties", {})


# --- sidebar grouping / ordering (Roadmap 5.7) ---


def test_contract_surfaces_category_and_nav_order_defaults():
    meta = build_model_contract(ArticleAdmin())
    assert meta.category is None
    assert meta.nav_order == 0


def test_contract_surfaces_explicit_category_and_nav_order():
    class CategorizedAdmin(ModelAdmin):
        model = Article
        category = "Content"
        nav_order = 5

    meta = build_model_contract(CategorizedAdmin())
    assert meta.category == "Content"
    assert meta.nav_order == 5


def test_order_sidebar_categories_config_then_alpha_then_system_last():
    from asterion.contract.service import order_sidebar_categories

    present = {"Sales", "Stock", "System", "Admin"}
    # Config pins Stock first; Admin/Sales fall in alphabetically; System last.
    assert order_sidebar_categories(present, ("Stock",)) == ["Stock", "Admin", "Sales", "System"]


def test_order_sidebar_categories_system_can_be_placed_explicitly():
    from asterion.contract.service import order_sidebar_categories

    assert order_sidebar_categories({"System", "Sales"}, ("System", "Sales")) == ["System", "Sales"]


def test_order_sidebar_categories_ignores_absent_config_entries():
    from asterion.contract.service import order_sidebar_categories

    # "Ghost" is configured but not present → skipped; only present ones returned.
    assert order_sidebar_categories({"Sales"}, ("Ghost", "Sales")) == ["Sales"]
