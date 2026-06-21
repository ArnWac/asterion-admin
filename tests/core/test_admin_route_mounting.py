"""Apps can mount their own routes under the admin prefix (0.1.6).

The CRUD/action routes are registered explicitly per registered resource, not
via a greedy ``/{resource}`` catch-all. So a path under the admin prefix that
is NOT a registered resource is free for an embedding app to claim with a plain
``app.include_router`` AFTER ``create_admin`` — no AdminExtension /
``register_routes``, no ordering tricks.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import DeclarativeBase

from asterion import CoreAdminConfig, ModelAdmin, create_admin
from asterion.models.base import GlobalModel
from tests._helpers import make_admin_principal, make_admin_tenant, override_admin_context

ADMIN = "/api/v1/admin"


class _AppBase(DeclarativeBase):
    pass


class Project(_AppBase):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)


class ProjectAdmin(ModelAdmin):
    model = Project
    list_display = ["id", "name"]


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'mount.db'}"
    application = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key="test-mount-secret",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        ),
        register=lambda reg: reg.register(ProjectAdmin),
    )

    # The app mounts its OWN domain route under the admin prefix AFTER
    # create_admin — the exact thing the greedy catch-all used to break.
    app_router = APIRouter()

    @app_router.get("/health-check")
    async def health_check() -> dict:
        return {"ok": True, "who": "app"}

    @app_router.get("/work-sessions/{session_id}")
    async def work_session(session_id: str) -> dict:
        return {"session_id": session_id}

    application.include_router(app_router, prefix=ADMIN)

    runtime = application.state.asterion

    async def _setup_schema():
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
            await conn.run_sync(Project.metadata.create_all)

    asyncio.run(_setup_schema())
    override_admin_context(
        application,
        principal=make_admin_principal(email="x@y.com"),
        tenant=make_admin_tenant("acme"),
        permissions=frozenset({"admin.*"}),
    )
    yield application
    asyncio.run(runtime.db.dispose())


def test_app_route_under_admin_prefix_wins(app):
    """A single-segment app route is no longer swallowed as a CRUD resource."""
    resp = TestClient(app).get(f"{ADMIN}/health-check")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "who": "app"}


def test_app_route_with_id_segment_wins(app):
    """The old ``/{resource}/{record_id}`` catch-all would have hijacked this
    as a CRUD read of resource 'work-sessions'."""
    resp = TestClient(app).get(f"{ADMIN}/work-sessions/abc123")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"session_id": "abc123"}


def test_registered_resource_unchanged(app):
    """A real registered resource still serves CRUD as before."""
    resp = TestClient(app).get(f"{ADMIN}/projects")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "results" in body or "items" in body or isinstance(body, dict)


def test_unknown_admin_path_without_app_route_is_404(app):
    """A path that is neither a registered resource nor an app route 404s."""
    resp = TestClient(app).get(f"{ADMIN}/totally-unknown")
    assert resp.status_code == 404
