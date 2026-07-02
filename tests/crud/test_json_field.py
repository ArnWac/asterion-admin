"""JSON/dict column round-trips through create + update (v0.1.50).

An embedding app (Simpletimes) hit a data-loss bug: a ``JSON`` column
(contract ``type: "string"``, ``widget: "json"``) could not be saved from the
admin UI. The form rendered the dict through a plain text input as the literal
``"[object Object]"`` and posted that string back, 500-ing the write. The fix
is client-side (``form.js`` now renders/parses a json ``<textarea>``); these
tests pin the server contract the fixed client relies on: the column is marked
``widget: "json"`` and a real ``dict`` round-trips through the CRUD services.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import JSON, Column, Integer, String
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from asterion.contract.service import build_model_contract
from asterion.crud.services import create_record, read_record, update_record
from asterion.registry import ModelAdmin


class _Base(DeclarativeBase):
    pass


class Doc(_Base):
    __tablename__ = "docs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    extra = Column(JSON, nullable=True)


class DocAdmin(ModelAdmin):
    model = Doc
    readonly_fields = ["id"]


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


def test_contract_marks_json_column_as_json_widget():
    contract = build_model_contract(DocAdmin())
    by_name = {f.name: f for f in contract.fields}
    assert by_name["payload"].widget == "json"
    # Wire-format type stays "string" (A2 compat) — the widget hint is what the
    # form uses to pick the json textarea.
    assert by_name["payload"].type == "string"


@pytest.mark.anyio
async def test_dict_round_trips_through_create_and_update(db_session: AsyncSession):
    created = await create_record(
        db_session, DocAdmin(), {"name": "one", "payload": {"a": 1, "nested": {"b": [2, 3]}}}
    )
    assert created["payload"] == {"a": 1, "nested": {"b": [2, 3]}}

    updated = await update_record(
        db_session, DocAdmin(), str(created["id"]), {"payload": {"a": 2}}
    )
    assert updated["payload"] == {"a": 2}

    found = await read_record(db_session, DocAdmin(), str(created["id"]))
    assert found["payload"] == {"a": 2}


@pytest.mark.anyio
async def test_empty_dict_and_null_round_trip(db_session: AsyncSession):
    # The fixed client sends {} for a non-nullable empty json field and null for
    # a nullable one; both must be accepted by the write path.
    created = await create_record(
        db_session, DocAdmin(), {"name": "two", "payload": {}, "extra": None}
    )
    assert created["payload"] == {}
    assert created["extra"] is None
