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
