"""FileField column + adapter (Roadmap P4.2).

Pins:
* the adapter recognises ``FileFieldType`` and only that — plain
  String columns stay with ``StringAdapter``;
* the adapter wins over ``StringAdapter`` in the default registry
  (FileFieldType extends String, the universal fallback would
  otherwise claim it);
* serialize / parse are identity on the key string + ``None``;
* end-to-end SQLAlchemy roundtrip on SQLite — the column persists as
  a plain VARCHAR with no driver-specific dance.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import Column, String
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from asterion.fields import (
    FileFieldAdapter,
    FileFieldType,
    build_default_registry,
)

# ---------------------------------------------------------------------------
# Adapter selection
# ---------------------------------------------------------------------------


def test_file_field_adapter_supports_file_field_type_column():
    col = Column("avatar", FileFieldType(), nullable=True)
    assert FileFieldAdapter().supports(col) is True


def test_file_field_adapter_does_not_claim_plain_string_column():
    """A regular String column must stay with StringAdapter — the
    FileField semantics are opt-in."""
    col = Column("name", String(100), nullable=True)
    assert FileFieldAdapter().supports(col) is False


def test_default_registry_picks_file_adapter_before_string():
    """FileFieldType extends String; the default registry must order
    the file adapter so the plain-String fallback can't shadow it."""
    registry = build_default_registry()
    col = Column("avatar", FileFieldType(), nullable=True)
    adapter = registry.find_adapter(col)
    assert adapter is not None
    assert adapter.name == "file"


def test_default_registry_still_picks_string_for_plain_string():
    registry = build_default_registry()
    col = Column("name", String(100), nullable=True)
    adapter = registry.find_adapter(col)
    assert adapter is not None
    assert adapter.name == "string"


# ---------------------------------------------------------------------------
# Contract shape
# ---------------------------------------------------------------------------


def test_contract_emits_type_file_and_file_widget():
    col = Column("avatar", FileFieldType(), nullable=True)
    contract = FileFieldAdapter().build_contract(col)
    assert contract.type == "file"
    assert contract.metadata.get("widget") == "file"
    assert contract.python_type is str
    assert contract.nullable is True


def test_contract_marks_pk_read_only():
    col = Column("file_key", FileFieldType(), primary_key=True)
    contract = FileFieldAdapter().build_contract(col)
    assert contract.primary_key is True
    assert contract.read_only is True


# ---------------------------------------------------------------------------
# serialize / parse purity
# ---------------------------------------------------------------------------


def test_serialize_passes_through_key_and_none():
    adapter = FileFieldAdapter()
    assert adapter.serialize("articles/2026/cover.png") == "articles/2026/cover.png"
    assert adapter.serialize(None) is None


def test_parse_passes_through_key_and_none():
    adapter = FileFieldAdapter()
    assert adapter.parse("articles/2026/cover.png") == "articles/2026/cover.png"
    assert adapter.parse(None) is None


def test_adapter_does_not_touch_runtime_or_storage():
    """Adapter is intentionally pure — building a contract / parsing a
    value must not require a configured StorageBackend on ``ctx``."""
    adapter = FileFieldAdapter()
    col = Column("avatar", FileFieldType(), nullable=True)
    # No ctx, no runtime, no storage — must just work.
    assert adapter.build_contract(col, ctx=None).type == "file"
    assert adapter.serialize("k", ctx=None) == "k"
    assert adapter.parse("k", ctx=None) == "k"


# ---------------------------------------------------------------------------
# End-to-end SQLAlchemy roundtrip
# ---------------------------------------------------------------------------


class _Base(DeclarativeBase):
    pass


class _Doc(_Base):
    __tablename__ = "_file_field_docs"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(100))
    attachment: Mapped[str | None] = mapped_column(FileFieldType(), nullable=True)


def test_file_field_roundtrips_through_sqlite(tmp_path):
    """The TypeDecorator is transparent: the column stores + returns a
    plain string. No driver-side coercion needed."""
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'file_field.db'}"
    engine = create_async_engine(db_url)

    async def _go():
        async with engine.begin() as conn:
            await conn.run_sync(_Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                session.add(_Doc(title="Hello", attachment="articles/2026/cover.png"))
                session.add(_Doc(title="Empty", attachment=None))

        async with factory() as session:
            rows = (await session.execute(sa.select(_Doc).order_by(_Doc.id))).scalars().all()
            return [(r.title, r.attachment) for r in rows]

    result = asyncio.run(_go())
    assert result == [
        ("Hello", "articles/2026/cover.png"),
        ("Empty", None),
    ]
