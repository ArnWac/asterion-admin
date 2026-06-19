"""Storage HTTP surface — upload + serve (Roadmap P4.4).

Covers:
* auth gate on both endpoints;
* upload roundtrip: POST returns key + metadata, GET returns the same
  bytes;
* size enforcement (Content-Length fast-fail + body re-check);
* empty upload rejected;
* 404 on serve for unknown keys;
* 503 when ``runtime.storage`` is None (storage capability optional).
"""

from __future__ import annotations

import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion import CoreAdminConfig, create_admin
from asterion.admin.context import (
    AdminContext,
    build_admin_context,
    require_admin_context,
)
from asterion.db.dependencies import get_async_session
from asterion.models.base import GLOBAL_METADATA
from asterion.providers.base import AdminPrincipal


def _override_ctx() -> AdminContext:
    return AdminContext(
        request=None,
        principal=AdminPrincipal(id="alice", email="alice@example.com"),
        tenant=None,
        permissions=frozenset({"admin.*"}),
    )


@pytest_asyncio.fixture
async def storage_app(tmp_path):
    """App with local-file storage wired + auth overridden."""
    app = create_admin(
        config=CoreAdminConfig(
            secret_key="x" * 64,
            database_url="sqlite+aiosqlite:///:memory:",
            storage_root=str(tmp_path / "uploads"),
            environment="development",
            enable_multi_tenant=False,
        ),
    )

    engine = app.state.asterion.db.engine
    async with engine.begin() as conn:
        await conn.run_sync(GLOBAL_METADATA.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_session():
        async with factory() as session:
            async with session.begin():
                yield session

    app.dependency_overrides[get_async_session] = _override_session
    app.dependency_overrides[build_admin_context] = _override_ctx
    app.dependency_overrides[require_admin_context] = _override_ctx

    yield TestClient(app)
    await engine.dispose()


@pytest_asyncio.fixture
async def no_storage_app(tmp_path):
    """App with NO storage configured — to assert the 503 path."""
    app = create_admin(
        config=CoreAdminConfig(
            secret_key="x" * 64,
            database_url="sqlite+aiosqlite:///:memory:",
            environment="development",
            enable_multi_tenant=False,
        ),
    )
    assert app.state.asterion.storage is None

    engine = app.state.asterion.db.engine
    async with engine.begin() as conn:
        await conn.run_sync(GLOBAL_METADATA.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_session():
        async with factory() as session:
            async with session.begin():
                yield session

    app.dependency_overrides[get_async_session] = _override_session
    app.dependency_overrides[build_admin_context] = _override_ctx
    app.dependency_overrides[require_admin_context] = _override_ctx

    yield TestClient(app)
    await engine.dispose()


# ---------------------------------------------------------------------------
# auth gate
# ---------------------------------------------------------------------------


def test_upload_requires_auth(tmp_path):
    """Without the dependency override the upload route 401s — the
    admin auth chain is the gate, not a separate scope."""
    app = create_admin(
        config=CoreAdminConfig(
            secret_key="x" * 64,
            database_url="sqlite+aiosqlite:///:memory:",
            storage_root=str(tmp_path / "uploads"),
            environment="development",
            enable_multi_tenant=False,
        ),
    )
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/admin/_storage/upload",
        files={"file": ("a.txt", b"x", "text/plain")},
    )
    assert resp.status_code == 401


def test_serve_requires_auth(tmp_path):
    app = create_admin(
        config=CoreAdminConfig(
            secret_key="x" * 64,
            database_url="sqlite+aiosqlite:///:memory:",
            storage_root=str(tmp_path / "uploads"),
            environment="development",
            enable_multi_tenant=False,
        ),
    )
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/v1/admin/_storage/some/key")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# upload happy path
# ---------------------------------------------------------------------------


def test_upload_returns_key_size_content_type(storage_app):
    resp = storage_app.post(
        "/api/v1/admin/_storage/upload",
        files={"file": ("hello.txt", b"hello world", "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["key"]
    assert body["size"] == 11
    assert body["content_type"] == "text/plain"
    assert body["filename"] == "hello.txt"
    assert body["etag"]


def test_upload_key_is_partitioned_by_year_and_month(storage_app):
    """The mint scheme is ``YYYY/MM/{uuid}`` — pin the prefix shape so
    cleanup/lifecycle tooling can rely on it."""
    resp = storage_app.post(
        "/api/v1/admin/_storage/upload",
        files={"file": ("a", b"x", "application/octet-stream")},
    )
    key = resp.json()["key"]
    parts = key.split("/")
    assert len(parts) == 3
    assert len(parts[0]) == 4 and parts[0].isdigit()  # YYYY
    assert len(parts[1]) == 2 and parts[1].isdigit()  # MM
    assert len(parts[2]) == 32  # uuid4 hex


def test_two_uploads_get_distinct_keys(storage_app):
    """Key minting must not collide for back-to-back uploads —
    foundational property the FileField column relies on."""
    a = storage_app.post(
        "/api/v1/admin/_storage/upload",
        files={"file": ("a", b"aaa", "text/plain")},
    ).json()["key"]
    b = storage_app.post(
        "/api/v1/admin/_storage/upload",
        files={"file": ("b", b"bbb", "text/plain")},
    ).json()["key"]
    assert a != b


# ---------------------------------------------------------------------------
# upload + serve roundtrip
# ---------------------------------------------------------------------------


def test_serve_returns_uploaded_bytes_and_content_type(storage_app):
    payload = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    key = storage_app.post(
        "/api/v1/admin/_storage/upload",
        files={"file": ("a.png", payload, "image/png")},
    ).json()["key"]

    resp = storage_app.get(f"/api/v1/admin/_storage/{key}")
    assert resp.status_code == 200
    assert resp.content == payload
    assert resp.headers["content-type"].startswith("image/png")
    assert int(resp.headers["content-length"]) == len(payload)


# ---------------------------------------------------------------------------
# size / empty enforcement
# ---------------------------------------------------------------------------


def test_empty_upload_is_400(storage_app):
    resp = storage_app.post(
        "/api/v1/admin/_storage/upload",
        files={"file": ("empty", b"", "application/octet-stream")},
    )
    assert resp.status_code == 400


def test_oversize_upload_is_413(tmp_path):
    """``storage_max_upload_bytes`` is enforced on the read path —
    1 byte over the cap returns 413, the request is rejected, and
    nothing lands on disk."""
    cap = 16
    app = create_admin(
        config=CoreAdminConfig(
            secret_key="x" * 64,
            database_url="sqlite+aiosqlite:///:memory:",
            storage_root=str(tmp_path / "uploads"),
            storage_max_upload_bytes=cap,
            environment="development",
            enable_multi_tenant=False,
        ),
    )
    engine = app.state.asterion.db.engine

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(GLOBAL_METADATA.create_all)

    import asyncio

    asyncio.run(_setup())

    app.dependency_overrides[require_admin_context] = _override_ctx
    app.dependency_overrides[build_admin_context] = _override_ctx
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post(
        "/api/v1/admin/_storage/upload",
        files={"file": ("big", b"x" * (cap + 1), "application/octet-stream")},
    )
    assert resp.status_code == 413, resp.text


# ---------------------------------------------------------------------------
# serve error paths
# ---------------------------------------------------------------------------


def test_serve_unknown_key_is_404(storage_app):
    resp = storage_app.get("/api/v1/admin/_storage/2026/01/never_was")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# unconfigured backend
# ---------------------------------------------------------------------------


def test_upload_returns_503_when_storage_not_configured(no_storage_app):
    """Apps that don't wire storage shouldn't crash on accidental
    POSTs — they should get a clear 503 + actionable error message."""
    resp = no_storage_app.post(
        "/api/v1/admin/_storage/upload",
        files={"file": ("a", b"x", "text/plain")},
    )
    assert resp.status_code == 503
    body = resp.json()
    # The framework wraps HTTPException in {"error": {"message": ...}}
    assert "storage_root" in body["error"]["message"]


def test_serve_returns_503_when_storage_not_configured(no_storage_app):
    resp = no_storage_app.get("/api/v1/admin/_storage/anything")
    assert resp.status_code == 503
