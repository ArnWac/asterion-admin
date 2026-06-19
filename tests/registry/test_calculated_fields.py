"""Phase 5 — Django-like calculated fields.

Verifies the canonical ``calculated_fields`` API end-to-end:
 * declared on ModelAdmin via ``calculated_fields = {name: callable}``
 * surfaced in the contract with ``calculated=True`` and ``read_only=True``
 * emitted by the serializer on both list and detail responses
 * never writable — rejected on create AND update with 422
 * do not require a backing DB column
 * exceptions raised inside the callable are swallowed → ``null``
 * per-subclass isolation (one admin's calculated_fields don't leak into siblings)
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, String
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from asterion import CoreAdminConfig, ModelAdmin, create_admin
from asterion.auth.password import hash_password
from asterion.contract.service import (
    build_field_metadata,
    build_model_contract,
)
from asterion.crud.payload import clean_write_payload
from asterion.models.base import GlobalModel
from asterion.models.user import User
from asterion.schemas.builder import build_model_schema
from asterion.schemas.serialization.serializer import (
    serialize_record,
    serialize_records,
)
from tests._helpers import make_admin_principal, make_admin_tenant, override_admin_context


class _AppBase(DeclarativeBase):
    pass


class Article(_AppBase):
    __tablename__ = "articles"
    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False)
    body = Column(String, nullable=True)


class ArticleAdmin(ModelAdmin):
    model = Article
    list_display = ["id", "title", "word_count", "display_title"]
    search_fields = ["title"]
    ordering = ["id"]
    readonly_fields = ["id"]
    calculated_fields = {
        "word_count": lambda obj: len((obj.body or "").split()),
        "display_title": lambda obj: f"[{obj.id}] {obj.title}",
        "broken": lambda obj: 1 / 0,  # exercises error-swallow path
    }


class _StubArticle:
    """Stand-in instance that mimics the SQLAlchemy mapped behaviour without
    needing a real session — used for serializer unit tests."""

    __table__ = Article.__table__

    def __init__(self, *, id: int, title: str, body: str | None = None) -> None:
        self.id = id
        self.title = title
        self.body = body


# --- per-subclass default isolation ---


def test_calculated_fields_default_isolated_between_subclasses():
    class A(ModelAdmin):
        model = Article
        calculated_fields = {"a_only": lambda obj: "a"}

    class B(ModelAdmin):
        model = Article

    assert "a_only" in A.calculated_fields
    assert "a_only" not in B.calculated_fields
    assert B.calculated_fields == {}


def test_calculated_fields_default_is_empty_dict():
    class Bare(ModelAdmin):
        model = Article

    assert Bare.calculated_fields == {}


# --- contract metadata ---


def test_calculated_field_marked_in_contract():
    fields = build_field_metadata(ArticleAdmin())
    by_name = {f.name: f for f in fields}

    assert "word_count" in by_name
    assert by_name["word_count"].calculated is True
    assert by_name["word_count"].read_only is True

    assert "display_title" in by_name
    assert by_name["display_title"].calculated is True


def test_contract_includes_all_calculated_fields():
    contract = build_model_contract(ArticleAdmin())
    names = [f.name for f in contract.fields]
    assert "word_count" in names
    assert "display_title" in names
    assert "broken" in names


def test_calculated_field_not_in_underlying_columns():
    column_names = {c.name for c in Article.__table__.columns}
    assert "word_count" not in column_names
    assert "display_title" not in column_names


# --- serializer ---


def test_serializer_emits_calculated_fields():
    obj = _StubArticle(id=1, title="Hello", body="one two three")
    out = serialize_record(obj, ArticleAdmin())
    assert out["word_count"] == 3
    assert out["display_title"] == "[1] Hello"


def test_serializer_swallows_calculated_field_exception():
    obj = _StubArticle(id=1, title="Hello", body=None)
    out = serialize_record(obj, ArticleAdmin())
    assert out["broken"] is None


def test_serializer_list_emits_calculated_fields():
    objs = [
        _StubArticle(id=1, title="A", body="alpha"),
        _StubArticle(id=2, title="B", body="alpha beta gamma"),
    ]
    out = serialize_records(objs, ArticleAdmin())
    assert out[0]["word_count"] == 1
    assert out[1]["word_count"] == 3


# --- write rejection ---


def test_calculated_field_rejected_on_create():
    schema = build_model_schema(ArticleAdmin())
    with pytest.raises(Exception) as exc:
        clean_write_payload({"title": "x", "word_count": 99}, schema, partial=False)
    assert exc.value.status_code == 422


def test_calculated_field_rejected_on_update():
    schema = build_model_schema(ArticleAdmin())
    with pytest.raises(Exception) as exc:
        clean_write_payload({"display_title": "Hijack"}, schema, partial=True)
    assert exc.value.status_code == 422


def test_writable_payload_unaffected_by_calculated_fields():
    schema = build_model_schema(ArticleAdmin())
    cleaned = clean_write_payload({"title": "Hello", "body": "text"}, schema, partial=False)
    assert cleaned == {"title": "Hello", "body": "text"}


# --- HTTP integration ---


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'calc.db'}"
    application = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key="test-calc-secret",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        ),
        register=lambda reg: reg.register(ArticleAdmin),
    )

    runtime = application.state.asterion

    async def _setup():
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
            await conn.run_sync(Article.metadata.create_all)
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                session.add(
                    User(
                        email="user@example.com",
                        hashed_password=hash_password("hunter2-strong"),
                        is_active=True,
                    )
                )
                session.add(Article(title="One", body="alpha"))
                session.add(Article(title="Two", body="alpha beta gamma"))

    asyncio.run(_setup())

    override_admin_context(
        application,
        principal=make_admin_principal(email="user@example.com"),
        tenant=make_admin_tenant("acme"),
        permissions=frozenset({"admin.*"}),
    )

    yield application

    asyncio.run(runtime.db.dispose())


@pytest.fixture
def client(app):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_http_list_includes_calculated_fields(client):
    body = client.get("/api/v1/admin/articles").json()
    assert body["total"] == 2
    item0, item1 = body["items"]
    assert item0["word_count"] == 1
    assert item1["word_count"] == 3
    assert item0["display_title"].startswith("[")


def test_http_detail_includes_calculated_fields(client):
    list_body = client.get("/api/v1/admin/articles").json()
    first_id = list_body["items"][0]["id"]
    body = client.get(f"/api/v1/admin/articles/{first_id}").json()
    assert "word_count" in body
    assert "display_title" in body


def test_http_create_rejects_calculated_field(client):
    resp = client.post(
        "/api/v1/admin/articles",
        json={"title": "x", "body": "y", "word_count": 999},
    )
    assert resp.status_code == 422


def test_http_update_rejects_calculated_field(client):
    list_body = client.get("/api/v1/admin/articles").json()
    first_id = list_body["items"][0]["id"]
    resp = client.patch(
        f"/api/v1/admin/articles/{first_id}",
        json={"display_title": "Hijack"},
    )
    assert resp.status_code == 422


def test_http_calculated_value_reflects_current_record_state(client):
    list_body = client.get("/api/v1/admin/articles").json()
    first_id = list_body["items"][0]["id"]
    # update body, then verify calculated word_count is recomputed
    resp = client.patch(
        f"/api/v1/admin/articles/{first_id}",
        json={"body": "one two three four five"},
    )
    assert resp.status_code == 200
    assert resp.json()["word_count"] == 5


def test_http_contract_endpoint_lists_calculated_fields(client):
    body = client.get("/api/v1/admin/_contract/articles").json()
    by_name = {f["name"]: f for f in body["fields"]}
    assert by_name["word_count"]["calculated"] is True
    assert by_name["word_count"]["read_only"] is True
    assert by_name["display_title"]["calculated"] is True
