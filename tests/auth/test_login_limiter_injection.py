"""The injected login rate limiter is used on every path (Review R17).

R7 added ``create_admin(login_rate_limiter=...)`` but the login route initially
still called the in-memory default on the success path (``clear``). This test
pins that a custom backend sees ``is_limited`` + ``record_failure`` on a failed
login and ``clear`` on a successful one — the regression that slipped through
R7 would fail here.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion import CoreAdminConfig, create_admin
from asterion.auth.password import hash_password
from asterion.models.base import GlobalModel
from asterion.models.user import User

SECRET = "test-limiter-injection-secret"
PASSWORD = "hunter2-strong"


class RecordingLimiter:
    """Records which methods the login flow calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def is_limited(self, key: str) -> bool:
        self.calls.append(("is_limited", key))
        return False

    async def record_failure(self, key: str) -> None:
        self.calls.append(("record_failure", key))

    async def clear(self, key: str) -> None:
        self.calls.append(("clear", key))

    def methods(self) -> set[str]:
        return {m for m, _ in self.calls}


@pytest.fixture
def app_and_limiter(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'limiter.db'}"
    limiter = RecordingLimiter()
    application = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key=SECRET,
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        ),
        login_rate_limiter=limiter,
    )
    runtime = application.state.asterion

    async def _setup():
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                session.add(
                    User(
                        email="alice@example.com",
                        hashed_password=hash_password(PASSWORD),
                        is_active=True,
                    )
                )

    asyncio.run(_setup())
    yield application, limiter
    asyncio.run(runtime.db.dispose())


def test_failed_login_records_on_injected_limiter(app_and_limiter):
    app, limiter = app_and_limiter
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "wrong-password"},
    )
    assert resp.status_code == 401, resp.text
    assert "is_limited" in limiter.methods()
    assert "record_failure" in limiter.methods()


def test_successful_login_clears_on_injected_limiter(app_and_limiter):
    app, limiter = app_and_limiter
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    # The R7 regression cleared the in-memory default instead of this backend.
    assert "clear" in limiter.methods()
