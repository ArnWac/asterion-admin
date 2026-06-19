"""C2+: ``GET /<resource>/<id>`` includes the inline children.

Validates the read-path counterpart to C2's write-path inline writer:
the serialized parent carries an ``inlines`` block keyed by child
table name, with each row already serialized through the framework's
serializer (protected fields filtered out).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from asterion.admin import InlineAdmin
from asterion.admin.context import AdminContext
from asterion.crud.services import (
    create_record,
    list_records,
    read_record,
    update_record,
)
from asterion.providers.base import AdminPrincipal
from asterion.registry import ModelAdmin


class _Base(DeclarativeBase):
    pass


class _Recipe(_Base):
    __tablename__ = "c2r_recipes"
    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(100), nullable=False)


class _Ingredient(_Base):
    __tablename__ = "c2r_ingredients"
    id = Column(Integer, primary_key=True, autoincrement=True)
    recipe_id = Column(Integer, ForeignKey("c2r_recipes.id"), nullable=False)
    name = Column(String(100), nullable=False)
    amount = Column(String(40), nullable=True)


class _IngredientInline(InlineAdmin):
    model = _Ingredient
    fk_name = "recipe_id"
    fields = ["name", "amount"]
    ordering = ["id"]


class _RecipeAdmin(ModelAdmin):
    model = _Recipe
    readonly_fields = ["id"]
    inlines = [_IngredientInline]


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


def _ctx() -> AdminContext:
    return AdminContext(
        request=None,
        principal=AdminPrincipal(id="u1"),
        tenant=None,
    )


# ---------------------------------------------------------------------------
# read_record
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_read_returns_inlines_block(db_session):
    admin = _RecipeAdmin()
    created = await create_record(
        db_session,
        admin,
        {
            "title": "Bread",
            "inlines": {
                "c2r_ingredients": [
                    {"name": "flour", "amount": "500g"},
                    {"name": "water", "amount": "300ml"},
                ]
            },
        },
    )

    result = await read_record(db_session, admin, str(created["id"]))
    assert "inlines" in result
    ingredients = result["inlines"]["c2r_ingredients"]
    assert [i["name"] for i in ingredients] == ["flour", "water"]
    # Each child carries its scalar columns including the fk so the
    # client knows where to round-trip writes.
    assert all("recipe_id" in i for i in ingredients)
    assert all(i["recipe_id"] == created["id"] for i in ingredients)


@pytest.mark.anyio
async def test_read_inlines_respect_ordering(db_session):
    """The inline's ``ordering`` directive must drive the row order
    of the returned children. Insert in scrambled order, expect
    ordered output."""
    admin = _RecipeAdmin()
    created = await create_record(
        db_session,
        admin,
        {
            "title": "Cake",
            "inlines": {
                "c2r_ingredients": [
                    {"name": "sugar", "amount": "x"},
                    {"name": "egg", "amount": "y"},
                    {"name": "butter", "amount": "z"},
                ]
            },
        },
    )
    result = await read_record(db_session, admin, str(created["id"]))
    names = [i["name"] for i in result["inlines"]["c2r_ingredients"]]
    # Ordering is by ``id`` ascending, so insertion order wins.
    assert names == ["sugar", "egg", "butter"]


@pytest.mark.anyio
async def test_read_empty_inlines_returns_empty_list_per_table(db_session):
    """A parent with no children still gets the ``inlines`` block —
    same shape every time, so the UI can iterate without checks."""
    admin = _RecipeAdmin()
    created = await create_record(db_session, admin, {"title": "Empty"})
    result = await read_record(db_session, admin, str(created["id"]))
    assert result["inlines"] == {"c2r_ingredients": []}


@pytest.mark.anyio
async def test_admin_without_inlines_has_no_inlines_block(db_session):
    """A read for an admin with no ``inlines`` declared must NOT
    inject the ``inlines`` key — keeps the wire surface minimal for
    the common case."""

    class _Bare(ModelAdmin):
        model = _Recipe
        readonly_fields = ["id"]

    admin = _Bare()
    created = await create_record(db_session, admin, {"title": "Plain"})
    result = await read_record(db_session, admin, str(created["id"]))
    assert "inlines" not in result


# ---------------------------------------------------------------------------
# Create + update returns include inlines
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_response_includes_inlines(db_session):
    """The C2+ contract: POST also returns the newly-written inline
    rows so the client doesn't need a follow-up GET."""
    admin = _RecipeAdmin()
    result = await create_record(
        db_session,
        admin,
        {
            "title": "Hi",
            "inlines": {"c2r_ingredients": [{"name": "salt"}]},
        },
    )
    assert "inlines" in result
    assert [i["name"] for i in result["inlines"]["c2r_ingredients"]] == ["salt"]


@pytest.mark.anyio
async def test_update_response_includes_inlines(db_session):
    admin = _RecipeAdmin()
    created = await create_record(
        db_session,
        admin,
        {"title": "Hi", "inlines": {"c2r_ingredients": [{"name": "a"}]}},
    )
    result = await update_record(
        db_session,
        admin,
        str(created["id"]),
        {"inlines": {"c2r_ingredients": [{"name": "b"}]}},
        ctx=_ctx(),
    )
    names = [i["name"] for i in result["inlines"]["c2r_ingredients"]]
    assert sorted(names) == ["a", "b"]


# ---------------------------------------------------------------------------
# list_records does NOT load inlines (N+1 protection)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_response_omits_inlines(db_session):
    """List pages must not load inline children — that would be an
    N+1 query per row. Clients that need the children should hit the
    detail endpoint."""
    admin = _RecipeAdmin()
    await create_record(
        db_session,
        admin,
        {"title": "A", "inlines": {"c2r_ingredients": [{"name": "x"}]}},
    )
    listing = await list_records(db_session, admin)
    assert listing["total"] == 1
    for row in listing["items"]:
        assert "inlines" not in row
