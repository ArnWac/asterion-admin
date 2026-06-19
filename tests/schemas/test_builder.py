"""Tests for SchemaBuilder and build_model_schema."""

from __future__ import annotations

from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import DeclarativeBase

from asterion.registry import ModelAdmin
from asterion.schemas.builder import build_model_schema


class _Base(DeclarativeBase):
    pass


class Widget(_Base):
    __tablename__ = "widgets"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    secret = Column(String(255), nullable=True)


class WidgetAdmin(ModelAdmin):
    model = Widget
    list_display = ["id", "name"]
    readonly_fields = ["id"]
    protected_fields = ["secret"]


def test_build_model_schema_fields():
    schema = build_model_schema(WidgetAdmin())
    names = [f.name for f in schema.fields]
    assert "id" in names
    assert "name" in names
    assert "secret" not in names  # hidden


def test_primary_key_marked():
    schema = build_model_schema(WidgetAdmin())
    id_field = next(f for f in schema.fields if f.name == "id")
    assert id_field.primary_key is True
    assert id_field.read_only is True


def test_readonly_field_marked():
    schema = build_model_schema(WidgetAdmin())
    id_field = next(f for f in schema.fields if f.name == "id")
    assert id_field.read_only is True


def test_hidden_field_excluded():
    schema = build_model_schema(WidgetAdmin())
    assert not any(f.name == "secret" for f in schema.fields)
