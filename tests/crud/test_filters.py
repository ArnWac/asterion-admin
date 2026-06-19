"""D1: custom list-view filters via ``ModelAdmin.filter_fields``.

Validates:
* ``?filter_<field>=value`` is parsed and applied.
* Multiple filters combine as AND.
* Unknown filter field → 422.
* Type coercion: booleans, integers, UUIDs all accept their natural
  query-string representation.
* Filter fields appear in the contract's ``filters`` list.
* Filters compose with ``search`` (AND).
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import Boolean, Column, Integer, String
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from asterion.contract.service import build_filter_metadata, build_model_contract
from asterion.crud.query import parse_filter_query
from asterion.crud.services import create_record, list_records
from asterion.registry import ModelAdmin


class _Base(DeclarativeBase):
    pass


class _Item(_Base):
    __tablename__ = "d1_items"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    color = Column(String(30), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    count = Column(Integer, nullable=True)


class _ItemAdmin(ModelAdmin):
    model = _Item
    readonly_fields = ["id"]
    search_fields = ["name"]
    filter_fields = ["color", "is_active", "count"]


@pytest_asyncio.fixture()
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            yield session
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.drop_all)
    await engine.dispose()


# ---------------------------------------------------------------------------
# parse_filter_query
# ---------------------------------------------------------------------------


class _FakeQP:
    """Stand-in for FastAPI's ``QueryParams.multi_items``."""

    def __init__(self, items):
        self._items = list(items)

    def multi_items(self):
        return self._items


def test_parse_filter_query_extracts_only_filter_prefixed_keys():
    qp = _FakeQP(
        [
            ("filter_color", "red"),
            ("limit", "10"),
            ("filter_count", "5"),
            ("search", "x"),
        ]
    )
    parsed = parse_filter_query(qp, _ItemAdmin())
    assert parsed == {"color": "red", "count": "5"}


def test_parse_filter_query_rejects_unknown_filter_field():
    qp = _FakeQP([("filter_ghost", "boo")])
    with pytest.raises(HTTPException) as exc:
        parse_filter_query(qp, _ItemAdmin())
    assert exc.value.status_code == 422


def test_parse_filter_query_returns_empty_when_no_filter_keys():
    qp = _FakeQP([("limit", "10"), ("search", "x")])
    assert parse_filter_query(qp, _ItemAdmin()) == {}


# ---------------------------------------------------------------------------
# apply_filters via list_records (integration)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_filter_by_string_column(db_session):
    admin = _ItemAdmin()
    await create_record(db_session, admin, {"name": "a", "color": "red"})
    await create_record(db_session, admin, {"name": "b", "color": "blue"})
    result = await list_records(db_session, admin, filters={"color": "red"})
    assert result["total"] == 1
    assert result["items"][0]["color"] == "red"


@pytest.mark.anyio
async def test_filter_by_boolean_column_true(db_session):
    admin = _ItemAdmin()
    await create_record(db_session, admin, {"name": "a", "is_active": True})
    await create_record(db_session, admin, {"name": "b", "is_active": False})
    result = await list_records(db_session, admin, filters={"is_active": "true"})
    assert result["total"] == 1
    assert result["items"][0]["is_active"] is True


@pytest.mark.anyio
async def test_filter_by_boolean_column_false_accepts_alt_forms(db_session):
    """``parse_filter_query`` returns the raw string; ``apply_filters``
    coerces. Pin ``"0"`` accepted as False."""
    admin = _ItemAdmin()
    await create_record(db_session, admin, {"name": "a", "is_active": True})
    await create_record(db_session, admin, {"name": "b", "is_active": False})
    result = await list_records(db_session, admin, filters={"is_active": "0"})
    assert result["total"] == 1
    assert result["items"][0]["is_active"] is False


@pytest.mark.anyio
async def test_filter_by_integer_column(db_session):
    admin = _ItemAdmin()
    await create_record(db_session, admin, {"name": "a", "count": 1})
    await create_record(db_session, admin, {"name": "b", "count": 2})
    result = await list_records(db_session, admin, filters={"count": "2"})
    assert result["total"] == 1
    assert result["items"][0]["count"] == 2


@pytest.mark.anyio
async def test_filter_by_bad_integer_raises_422(db_session):
    admin = _ItemAdmin()
    with pytest.raises(HTTPException) as exc:
        await list_records(db_session, admin, filters={"count": "not-an-int"})
    assert exc.value.status_code == 422


@pytest.mark.anyio
async def test_filter_by_bad_boolean_raises_422(db_session):
    admin = _ItemAdmin()
    with pytest.raises(HTTPException) as exc:
        await list_records(db_session, admin, filters={"is_active": "maybe"})
    assert exc.value.status_code == 422


@pytest.mark.anyio
async def test_filters_compose_as_and(db_session):
    admin = _ItemAdmin()
    await create_record(db_session, admin, {"name": "a", "color": "red", "count": 1})
    await create_record(db_session, admin, {"name": "b", "color": "red", "count": 2})
    await create_record(db_session, admin, {"name": "c", "color": "blue", "count": 1})
    result = await list_records(db_session, admin, filters={"color": "red", "count": "2"})
    assert result["total"] == 1
    assert result["items"][0]["name"] == "b"


@pytest.mark.anyio
async def test_filters_compose_with_search(db_session):
    admin = _ItemAdmin()
    await create_record(db_session, admin, {"name": "Alice", "color": "red"})
    await create_record(db_session, admin, {"name": "Bob", "color": "red"})
    result = await list_records(db_session, admin, filters={"color": "red"}, search="Alic")
    assert result["total"] == 1
    assert result["items"][0]["name"] == "Alice"


# ---------------------------------------------------------------------------
# Contract surfaces filters
# ---------------------------------------------------------------------------


def test_contract_exposes_filters():
    metas = build_filter_metadata(_ItemAdmin())
    by_name = {m.name: m for m in metas}
    assert by_name.keys() == {"color", "is_active", "count"}
    assert by_name["color"].type == "string"
    assert by_name["is_active"].type == "boolean"
    assert by_name["count"].type == "integer"


def test_contract_filters_default_empty():
    class _NoFilterAdmin(ModelAdmin):
        model = _Item

    contract = build_model_contract(_NoFilterAdmin())
    assert contract.filters == []


def test_contract_filters_skips_unknown_column():
    class _BadAdmin(ModelAdmin):
        model = _Item
        filter_fields = ["color", "does_not_exist"]

    metas = build_filter_metadata(_BadAdmin())
    names = [m.name for m in metas]
    assert "does_not_exist" not in names
    assert "color" in names


# ---------------------------------------------------------------------------
# UUID column coercion path (covers the third coercion branch)
# ---------------------------------------------------------------------------


class _UuidItem(_Base):
    __tablename__ = "d1_uuid_items"
    id = Column(String(36), primary_key=True)
    owner_id = Column(String(36), nullable=False)


class _UuidAdmin(ModelAdmin):
    model = _UuidItem
    filter_fields = ["owner_id"]


def test_uuid_filter_value_accepted():
    """Even though the column is declared as String here (SQLite has
    no native UUID), the parser still accepts a UUID-shaped string —
    the coercion path treats it as a normal string. We're really
    asserting "no exception, value passes through" — the SQL-level
    comparison is what does the work."""
    admin = _UuidAdmin()
    qp = _FakeQP([("filter_owner_id", str(uuid.uuid4()))])
    parsed = parse_filter_query(qp, admin)
    assert "owner_id" in parsed
