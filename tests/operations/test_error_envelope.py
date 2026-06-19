"""Tests for the consistent error envelope (plan §PR-5)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from asterion import CoreAdminConfig, create_admin
from asterion.auth.password import hash_password
from asterion.core.errors import (
    AUTHENTICATION_REQUIRED,
    CONFLICT,
    FORBIDDEN,
    INTERNAL_ERROR,
    INVALID_TOKEN,
    NOT_FOUND,
    RATE_LIMITED,
    VALIDATION_ERROR,
    AdminError,
)
from asterion.core.middleware import REQUEST_ID_HEADER


def _envelope(resp):
    return resp.json()["error"]


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'errors.db'}"
    application = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key="test-errors-secret",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        )
    )
    return application


@pytest.fixture
def client(app):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# --- envelope shape ---


def test_404_has_envelope_shape(client):
    resp = client.get("/no-such-route")
    assert resp.status_code == 404
    body = resp.json()
    assert "error" in body
    err = body["error"]
    assert err["code"] == NOT_FOUND
    assert isinstance(err["message"], str)
    assert "request_id" in err


def test_envelope_includes_request_id_from_middleware(client):
    resp = client.get("/no-such-route", headers={REQUEST_ID_HEADER: "trace-xyz"})
    assert _envelope(resp)["request_id"] == "trace-xyz"


# --- status code → default code mapping ---


def test_status_codes_map_to_expected_codes(app):
    """Mount four probe routes covering 403/404/409/422."""

    @app.get("/p/403")
    async def p403():
        raise HTTPException(status_code=403, detail="nope")

    @app.get("/p/404")
    async def p404():
        raise HTTPException(status_code=404, detail="gone")

    @app.get("/p/409")
    async def p409():
        raise HTTPException(status_code=409, detail="busy")

    @app.get("/p/422")
    async def p422():
        raise HTTPException(status_code=422, detail="bad input")

    @app.get("/p/429")
    async def p429():
        raise HTTPException(status_code=429, detail="slow down")

    with TestClient(app, raise_server_exceptions=False) as client:
        assert _envelope(client.get("/p/403"))["code"] == FORBIDDEN
        assert _envelope(client.get("/p/404"))["code"] == NOT_FOUND
        assert _envelope(client.get("/p/409"))["code"] == CONFLICT
        assert _envelope(client.get("/p/422"))["code"] == VALIDATION_ERROR
        assert _envelope(client.get("/p/429"))["code"] == RATE_LIMITED


def test_401_distinguishes_missing_vs_invalid_token(app):
    @app.get("/p/401-required")
    async def p1():
        raise HTTPException(status_code=401, detail="Authentication required.")

    @app.get("/p/401-bad")
    async def p2():
        raise HTTPException(status_code=401, detail="Invalid access token.")

    with TestClient(app, raise_server_exceptions=False) as client:
        assert _envelope(client.get("/p/401-required"))["code"] == AUTHENTICATION_REQUIRED
        assert _envelope(client.get("/p/401-bad"))["code"] == INVALID_TOKEN


# --- AdminError ---


def test_admin_error_propagates_code(app):
    @app.get("/p/admin-error")
    async def custom():
        raise AdminError(
            status_code=409,
            code="tenant_inactive",
            message="Tenant is inactive.",
        )

    with TestClient(app, raise_server_exceptions=False) as client:
        err = _envelope(client.get("/p/admin-error"))
    assert err["code"] == "tenant_inactive"
    assert err["message"] == "Tenant is inactive."


def test_admin_error_carries_fields(app):
    @app.get("/p/admin-error-fields")
    async def custom():
        raise AdminError(
            status_code=422,
            code=VALIDATION_ERROR,
            message="Bad fields.",
            fields=[{"name": "email", "message": "bad email"}],
        )

    with TestClient(app, raise_server_exceptions=False) as client:
        err = _envelope(client.get("/p/admin-error-fields"))
    assert err["fields"] == [{"name": "email", "message": "bad email"}]


# --- legacy detail shapes still produce envelope ---


def test_string_detail_lands_as_message(app):
    @app.get("/p/string-detail")
    async def x():
        raise HTTPException(status_code=403, detail="Missing perm: admin.foo")

    with TestClient(app, raise_server_exceptions=False) as client:
        err = _envelope(client.get("/p/string-detail"))
    assert err["code"] == FORBIDDEN
    assert err["message"] == "Missing perm: admin.foo"


def test_crud_legacy_dict_detail_becomes_fields(app):
    """``clean_write_payload`` raises HTTPException(detail={'message','fields'})."""

    @app.get("/p/dict-detail")
    async def x():
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Payload contains non-writable fields.",
                "fields": ["password", "hashed_password"],
            },
        )

    with TestClient(app, raise_server_exceptions=False) as client:
        err = _envelope(client.get("/p/dict-detail"))
    assert err["code"] == VALIDATION_ERROR
    assert err["message"] == "Payload contains non-writable fields."
    assert err["fields"] == [
        {"name": "password", "message": "Invalid field."},
        {"name": "hashed_password", "message": "Invalid field."},
    ]


# --- request validation errors ---


class _LoginBody(BaseModel):
    email: str
    password: str


def test_request_validation_error_uses_envelope(app):
    @app.post("/p/validate")
    async def x(body: _LoginBody):
        return body

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post("/p/validate", json={"email": "x@y.com"})
    assert resp.status_code == 422
    err = _envelope(resp)
    assert err["code"] == VALIDATION_ERROR
    assert isinstance(err["fields"], list)
    names = {f["name"] for f in err["fields"]}
    assert "password" in names


# --- unhandled exception ---


def test_unhandled_exception_becomes_internal_error(app):
    @app.get("/p/boom")
    async def x():
        raise RuntimeError("undeclared crash")

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/p/boom")
    assert resp.status_code == 500
    err = _envelope(resp)
    assert err["code"] == INTERNAL_ERROR
    # Don't leak the exception message to clients.
    assert "undeclared crash" not in err["message"]


# --- existing endpoints emit envelope ---


def test_crud_unknown_resource_uses_envelope(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'crud-envelope.db'}"
    app = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key="test-crud-env",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        )
    )

    async def _user_stub():
        # bypass auth — we only care about the envelope
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from asterion.models.base import GlobalModel
        from asterion.models.user import User

        runtime = app.state.asterion
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                u = User(
                    email="x@y.com",
                    hashed_password=hash_password("hunter2-strong"),
                    is_active=True,
                )
                session.add(u)
            await session.refresh(u)
            return u

    import asyncio

    asyncio.run(_user_stub())

    from tests._helpers import make_admin_principal, make_admin_tenant, override_admin_context

    override_admin_context(
        app,
        principal=make_admin_principal(email="x@y.com"),
        tenant=make_admin_tenant("x"),
        permissions=frozenset({"admin.*"}),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/api/v1/admin/no_such_resource")

    assert resp.status_code == 404
    err = _envelope(resp)
    assert err["code"] == NOT_FOUND
    assert "no_such_resource" in err["message"] or "not registered" in err["message"].lower()
