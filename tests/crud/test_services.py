"""Integration tests for CRUD services using SQLite."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import Column, Integer, String
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from asterion.crud.services import (
    create_record,
    delete_record,
    list_records,
    read_record,
    update_record,
)
from asterion.registry import ModelAdmin


class _Base(DeclarativeBase):
    pass


class Tag(_Base):
    __tablename__ = "tags"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    color = Column(String(20), nullable=True)


class TagAdmin(ModelAdmin):
    model = Tag
    list_display = ["id", "name", "color"]
    readonly_fields = ["id"]
    search_fields = ["name"]
    ordering = ["name"]


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


@pytest.mark.anyio
async def test_create_and_read(db_session: AsyncSession):
    created = await create_record(db_session, TagAdmin(), {"name": "Python", "color": "blue"})
    assert created["name"] == "Python"
    assert "id" in created

    found = await read_record(db_session, TagAdmin(), str(created["id"]))
    assert found["name"] == "Python"


@pytest.mark.anyio
async def test_list_records(db_session: AsyncSession):
    await create_record(db_session, TagAdmin(), {"name": "Alpha"})
    await create_record(db_session, TagAdmin(), {"name": "Beta"})

    result = await list_records(db_session, TagAdmin())
    assert result["total"] >= 2


@pytest.mark.anyio
async def test_update_record(db_session: AsyncSession):
    created = await create_record(db_session, TagAdmin(), {"name": "Old"})
    updated = await update_record(db_session, TagAdmin(), str(created["id"]), {"name": "New"})
    assert updated["name"] == "New"


@pytest.mark.anyio
async def test_delete_record(db_session: AsyncSession):
    created = await create_record(db_session, TagAdmin(), {"name": "ToDelete"})
    result = await delete_record(db_session, TagAdmin(), str(created["id"]))
    assert result["deleted"] is True


@pytest.mark.anyio
async def test_list_with_search(db_session: AsyncSession):
    await create_record(db_session, TagAdmin(), {"name": "FindMe"})
    await create_record(db_session, TagAdmin(), {"name": "Other"})
    result = await list_records(db_session, TagAdmin(), search="FindMe")
    assert result["total"] == 1
