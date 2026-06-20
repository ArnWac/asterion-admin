"""C3 typed admin actions.

Validates:
* Legacy ``execute(records, session, user)`` actions still dispatch
  correctly — no behaviour change.
* New ``run(objects, data, ctx)`` actions receive a validated pydantic
  ``data`` instance.
* Invalid input → 422.
* ``to_dict`` exposes ``confirm`` / ``bulk`` / ``input_schema`` so the
  contract can render the form / confirm prompt.
"""

from __future__ import annotations

from typing import Any

import pytest_asyncio
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from asterion import create_admin
from asterion.actions import AdminAction, uses_typed_run
from asterion.core.config import CoreAdminConfig
from asterion.registry import ModelAdmin


class _Base(DeclarativeBase):
    pass


class _Widget(_Base):
    __tablename__ = "c3_widgets"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    color = Column(String(20), nullable=True)


# ---------------------------------------------------------------------------
# Helper detection: uses_typed_run
# ---------------------------------------------------------------------------


class _LegacyAction(AdminAction):
    name = "legacy"
    label = "Legacy"

    async def execute(self, records, session, user):
        return {"summary": "ok", "affected": len(records)}


class _TypedAction(AdminAction):
    name = "typed"
    label = "Typed"

    class Input(BaseModel):
        reason: str

    input_schema = Input

    async def run(self, objects, data, ctx):
        return {
            "summary": f"typed action ran with reason={data.reason!r}",
            "affected": len(objects),
        }


def test_uses_typed_run_detects_legacy_action():
    assert uses_typed_run(_LegacyAction()) is False


def test_uses_typed_run_detects_typed_action():
    assert uses_typed_run(_TypedAction()) is True


def test_to_dict_includes_input_schema_for_typed_action():
    out = _TypedAction().to_dict()
    assert out["input_schema"] is not None
    # pydantic JSON schema must mention the "reason" property.
    assert "reason" in str(out["input_schema"])


def test_to_dict_input_schema_none_when_not_declared():
    out = _LegacyAction().to_dict()
    assert out["input_schema"] is None


def test_to_dict_default_flags():
    out = _LegacyAction().to_dict()
    assert out["confirm"] is False
    assert out["bulk"] is True


class _ConfirmAction(AdminAction):
    name = "confirm_me"
    label = "Confirm me"
    confirm = True
    bulk = False

    async def execute(self, records, session, user):
        return {"summary": "ok", "affected": 0}


def test_to_dict_overrides_propagate():
    out = _ConfirmAction().to_dict()
    assert out["confirm"] is True
    assert out["bulk"] is False


# ---------------------------------------------------------------------------
# End-to-end through the action router
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.drop_all)
    await engine.dispose()


def _build_app_with_action(action: AdminAction, monkeypatch, session_factory):
    """Construct a minimal asterion app whose only registered admin
    points at ``_Widget`` and carries the given action.

    Auth dependencies are overridden to make every request pass the
    permission gate; the production wiring is exercised in
    ``tests/actions/test_router.py`` already."""

    from asterion.admin.context import (
        AdminContext,
        build_admin_context,
        require_admin_context,
    )
    from asterion.db.dependencies import get_async_session

    class _WidgetAdmin(ModelAdmin):
        model = _Widget
        readonly_fields = ["id"]
        actions = [action]

    def _register(registry):
        registry.register(_WidgetAdmin())

    app = create_admin(
        config=CoreAdminConfig(
            secret_key="x" * 64,
            database_url="sqlite+aiosqlite:///:memory:",
            environment="development",
        ),
        register=_register,
    )

    async def _override_session():
        async with session_factory() as session:
            async with session.begin():
                yield session

    async def _override_ctx() -> AdminContext:
        from asterion.providers.base import AdminPrincipal

        return AdminContext(
            request=None,
            principal=AdminPrincipal(id="test-user", email="t@x", is_superadmin=True),
            tenant=None,
            permissions=frozenset({"admin.*"}),
        )

    app.dependency_overrides[get_async_session] = _override_session
    app.dependency_overrides[build_admin_context] = _override_ctx
    app.dependency_overrides[require_admin_context] = _override_ctx

    return app


def test_typed_action_run_receives_validated_data(monkeypatch, session_factory):
    from fastapi.testclient import TestClient

    captured: dict[str, Any] = {}

    class _CapturingAction(AdminAction):
        name = "capture"
        label = "Capture"

        class Input(BaseModel):
            reason: str
            count: int = 1

        input_schema = Input

        async def run(self, objects, data, ctx):
            captured["reason"] = data.reason
            captured["count"] = data.count
            captured["ctx_principal_id"] = ctx.principal.id
            return {"summary": "captured", "affected": len(objects)}

    app = _build_app_with_action(_CapturingAction(), monkeypatch, session_factory)
    client = TestClient(app)

    response = client.post(
        "/api/v1/admin/c3_widgets/_actions/capture",
        json={"ids": [], "data": {"reason": "audit", "count": 5}},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"summary": "captured", "affected": 0}
    assert captured == {
        "reason": "audit",
        "count": 5,
        "ctx_principal_id": captured["ctx_principal_id"],  # any non-empty
    }


def test_typed_action_rejects_invalid_data(monkeypatch, session_factory):
    """Schema validation runs in the router; bad input → 422 before
    the action body executes."""
    from fastapi.testclient import TestClient

    fired = False

    class _StrictAction(AdminAction):
        name = "strict"
        label = "Strict"

        class Input(BaseModel):
            reason: str

        input_schema = Input

        async def run(self, objects, data, ctx):
            nonlocal fired
            fired = True
            return {"summary": "ran", "affected": 0}

    app = _build_app_with_action(_StrictAction(), monkeypatch, session_factory)
    client = TestClient(app)

    # Missing required field "reason".
    response = client.post(
        "/api/v1/admin/c3_widgets/_actions/strict",
        json={"ids": [], "data": {}},
    )
    assert response.status_code == 422
    assert fired is False


def test_legacy_action_still_dispatches_through_execute(monkeypatch, session_factory):
    """An action that overrides only ``execute`` must keep working —
    no behaviour change for the existing codebase."""
    from fastapi.testclient import TestClient

    called = {"execute": 0}

    class _LegacyEcho(AdminAction):
        name = "echo"
        label = "Echo"

        async def execute(self, records, session, user):
            called["execute"] += 1
            return {"summary": "echo", "affected": len(records)}

    app = _build_app_with_action(_LegacyEcho(), monkeypatch, session_factory)
    client = TestClient(app)

    response = client.post(
        "/api/v1/admin/c3_widgets/_actions/echo",
        json={"ids": []},
    )
    assert response.status_code == 200
    assert called["execute"] == 1
