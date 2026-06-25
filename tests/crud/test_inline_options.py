"""Dual-list inline options endpoint (Theme F).

``GET /api/v1/admin/{resource}/_inline_options/{inline}`` returns the universe
of ``{value, label}`` options for an inline declared with ``widget="dual_list"``
(e.g. all permission keys, all tenant members) — the source for the transfer
widget rendered in the parent's edit form. Authorization mirrors the FK-options
endpoint: the caller must be able to ``read`` the parent resource; the inline's
own resolver scopes the values it returns.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from asterion import CoreAdminConfig, ModelAdmin, create_admin
from asterion.admin.inline import InlineAdmin
from asterion.models.base import GlobalModel
from tests._helpers import make_admin_principal, make_admin_tenant, override_admin_context


class _AppBase(DeclarativeBase):
    pass


class Team(_AppBase):
    __tablename__ = "teams"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)


class TeamTag(_AppBase):
    __tablename__ = "team_tags"
    id = Column(Integer, primary_key=True, autoincrement=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    tag = Column(String(100), nullable=False)


class TeamTagInline(InlineAdmin):
    model = TeamTag
    fk_name = "team_id"
    label = "Tags"
    fields = ["tag"]
    widget = "dual_list"
    value_field = "tag"

    async def resolve_options(self, *, session, ctx=None, q=None, limit=1000):
        opts = [
            {"value": "alpha", "label": "alpha"},
            {"value": "beta", "label": "beta"},
            {"value": "gamma", "label": "gamma"},
        ]
        if q and q.strip():
            needle = q.strip().lower()
            opts = [o for o in opts if needle in o["label"]]
        return opts[:limit]


class TeamAdmin(ModelAdmin):
    model = Team
    list_display = ["id", "name"]
    inlines = [TeamTagInline]


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'inline_opts.db'}"
    application = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key="test-inline-opts-secret",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        ),
        register=lambda reg: reg.register(TeamAdmin),
    )
    runtime = application.state.asterion

    async def _setup():
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
            await conn.run_sync(_AppBase.metadata.create_all)
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                session.add(Team(name="Kitchen"))

    asyncio.run(_setup())
    yield application
    asyncio.run(runtime.db.dispose())


def _grant(app, keys: set[str]) -> None:
    override_admin_context(
        app,
        principal=make_admin_principal(email="user@example.com"),
        tenant=make_admin_tenant("acme"),
        permissions=frozenset(keys),
    )


def _client(app) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# --- contract surface ---


def test_contract_marks_inline_as_dual_list_with_value_field():
    from asterion.contract.service import build_model_contract

    contract = build_model_contract(TeamAdmin())
    inline = next(i for i in contract.inlines if i.model == "team_tags")
    assert inline.widget == "dual_list"
    assert inline.value_field == "tag"


def test_value_field_defaults_to_first_field_when_unset():
    from asterion.contract.service import build_model_contract

    class _Inline(InlineAdmin):
        model = TeamTag
        fk_name = "team_id"
        fields = ["tag"]
        widget = "dual_list"  # no explicit value_field

    class _Admin(ModelAdmin):
        model = Team
        inlines = [_Inline]

    inline = build_model_contract(_Admin()).inlines[0]
    assert inline.widget == "dual_list"
    assert inline.value_field == "tag"


# --- endpoint ---


def test_inline_options_returns_value_label_pairs(app):
    _grant(app, {"admin.teams.read"})
    resp = _client(app).get("/api/v1/admin/teams/_inline_options/team_tags")
    assert resp.status_code == 200
    assert [o["value"] for o in resp.json()["options"]] == ["alpha", "beta", "gamma"]


def test_inline_options_honours_search(app):
    _grant(app, {"admin.teams.read"})
    resp = _client(app).get("/api/v1/admin/teams/_inline_options/team_tags?q=bet")
    assert resp.status_code == 200
    assert [o["label"] for o in resp.json()["options"]] == ["beta"]


def test_inline_options_requires_read_on_parent(app):
    _grant(app, {"admin.teams.list"})  # no teams.read
    resp = _client(app).get("/api/v1/admin/teams/_inline_options/team_tags")
    assert resp.status_code == 403


def test_inline_options_unknown_inline_returns_404(app):
    _grant(app, {"admin.teams.read"})
    resp = _client(app).get("/api/v1/admin/teams/_inline_options/ghosts")
    assert resp.status_code == 404


def test_default_resolve_options_returns_none():
    class _Bare(InlineAdmin):
        model = TeamTag
        fk_name = "team_id"

    assert asyncio.run(_Bare().resolve_options(session=None)) is None
