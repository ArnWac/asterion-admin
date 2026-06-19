"""Tests for CRUD query helpers."""

from __future__ import annotations

import pytest
from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.orm import DeclarativeBase

from asterion.crud.query import (
    _parse_date_hierarchy,
    apply_date_hierarchy,
    apply_ordering,
    apply_search,
    coerce_primary_key_value,
    normalize_limit_offset,
    primary_key_column,
)
from asterion.registry import ModelAdmin


class _Base(DeclarativeBase):
    pass


class Product(_Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    name = Column(String(100))
    sku = Column(String(50))
    created_at = Column(DateTime)


class ProductAdmin(ModelAdmin):
    model = Product
    search_fields = ["name", "sku"]
    ordering = ["name"]


class DatedAdmin(ModelAdmin):
    model = Product
    date_hierarchy = "created_at"


# --- normalize_limit_offset ---


def test_normalize_limit_clamps_to_max():
    limit, offset = normalize_limit_offset(limit=9999, offset=0)
    assert limit <= 500


def test_normalize_offset_clamps_to_zero():
    _, offset = normalize_limit_offset(limit=10, offset=-5)
    assert offset == 0


# --- primary_key_column ---


def test_primary_key_column_found():
    col = primary_key_column(Product)
    assert col.name == "id"


# --- coerce_primary_key_value ---


def test_coerce_int_pk():
    val = coerce_primary_key_value(Product, "42")
    assert val == 42


def test_coerce_invalid_int_raises_422():
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        coerce_primary_key_value(Product, "not-an-int")


# --- apply_search ---


def test_apply_search_no_term_is_noop():
    from sqlalchemy import select

    stmt = select(Product)
    result = apply_search(stmt, ProductAdmin(), None)
    assert str(result) == str(stmt)


def test_apply_search_adds_where():
    from sqlalchemy import select

    stmt = select(Product)
    result = apply_search(stmt, ProductAdmin(), "test")
    assert "WHERE" in str(result).upper()


# --- apply_ordering ---


def test_apply_ordering_uses_admin_ordering():
    from sqlalchemy import select

    stmt = select(Product)
    result = apply_ordering(stmt, ProductAdmin())
    assert "ORDER BY" in str(result).upper()


def _order_clause(stmt) -> str:
    return str(stmt).upper().split("ORDER BY", 1)[1]


def test_apply_ordering_request_asc_adds_pk_tiebreaker():
    from sqlalchemy import select

    result = apply_ordering(select(Product), ProductAdmin(), "sku")
    clause = _order_clause(result)
    assert "PRODUCTS.SKU ASC" in clause
    assert clause.strip().endswith("PRODUCTS.ID ASC")


def test_apply_ordering_request_desc():
    from sqlalchemy import select

    clause = _order_clause(apply_ordering(select(Product), ProductAdmin(), "-sku"))
    assert "PRODUCTS.SKU DESC" in clause
    assert "PRODUCTS.ID ASC" in clause  # stable tiebreaker


def test_apply_ordering_request_by_pk_has_no_duplicate_tiebreaker():
    from sqlalchemy import select

    clause = _order_clause(apply_ordering(select(Product), ProductAdmin(), "-id"))
    assert "PRODUCTS.ID DESC" in clause
    assert clause.count("PRODUCTS.ID") == 1  # not appended twice


def test_apply_ordering_unknown_request_field_falls_back_to_default():
    from sqlalchemy import select

    clause = _order_clause(apply_ordering(select(Product), ProductAdmin(), "nonexistent"))
    # Falls back to admin.ordering = ["name"]; no request column, no tiebreaker.
    assert "PRODUCTS.NAME ASC" in clause
    assert "PRODUCTS.SKU" not in clause


# --- date hierarchy ---


def test_parse_date_hierarchy_year():
    import datetime as dt

    assert _parse_date_hierarchy("2026") == (dt.datetime(2026, 1, 1), dt.datetime(2027, 1, 1))


def test_parse_date_hierarchy_month():
    import datetime as dt

    assert _parse_date_hierarchy("2026-03") == (dt.datetime(2026, 3, 1), dt.datetime(2026, 4, 1))


def test_parse_date_hierarchy_december_rolls_to_next_year():
    import datetime as dt

    assert _parse_date_hierarchy("2026-12") == (dt.datetime(2026, 12, 1), dt.datetime(2027, 1, 1))


def test_parse_date_hierarchy_day():
    import datetime as dt

    assert _parse_date_hierarchy("2026-03-15") == (
        dt.datetime(2026, 3, 15),
        dt.datetime(2026, 3, 16),
    )


@pytest.mark.parametrize("bad", ["", "abcd", "2026-13", "2026-02-30", "2026-03-15-1", "20x6"])
def test_parse_date_hierarchy_invalid_returns_none(bad):
    assert _parse_date_hierarchy(bad) is None


def test_apply_date_hierarchy_adds_range_where():
    from sqlalchemy import select

    stmt = apply_date_hierarchy(select(Product), DatedAdmin(), "2026-03")
    sql = str(stmt).upper()
    assert "WHERE" in sql
    # Half-open range → two predicates on the date column in the WHERE clause.
    where_clause = sql.split("WHERE", 1)[1]
    assert where_clause.count("PRODUCTS.CREATED_AT") == 2
    assert ">=" in where_clause and "<" in where_clause


def test_apply_date_hierarchy_noop_without_admin_field():
    from sqlalchemy import select

    stmt = select(Product)
    assert str(apply_date_hierarchy(stmt, ProductAdmin(), "2026")) == str(stmt)


def test_apply_date_hierarchy_noop_for_unparseable_value():
    from sqlalchemy import select

    stmt = select(Product)
    assert str(apply_date_hierarchy(stmt, DatedAdmin(), "not-a-date")) == str(stmt)


def test_apply_date_hierarchy_noop_for_empty_value():
    from sqlalchemy import select

    stmt = select(Product)
    assert str(apply_date_hierarchy(stmt, DatedAdmin(), None)) == str(stmt)
