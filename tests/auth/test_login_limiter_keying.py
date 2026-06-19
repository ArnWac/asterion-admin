"""Login rate-limit keying: email vs (email, ip) (Review R15).

``login_rate_limit_by_ip`` is opt-in: off → the limiter key is the email; on →
the key is ``email|<client-ip>`` so one source can't lock a victim out across
every client.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion import CoreAdminConfig, create_admin
from asterion.auth.password import hash_password
from asterion.models.base import GlobalModel
from asterion.models.user import User

SECRET = "test-limiter-keying-secret"


class RecordingLimiter:
    def __init__(self) -> None:
        self.keys: list[str] = []

    async def is_limited(self, key: str) -> bool:
        self.keys.append(key)
        return False

    async def record_failure(self, key: str) -> None:
        self.keys.append(key)

    async def clear(self, key: str) -> None:
        self.keys.append(key)


def _build(tmp_path, *, by_ip: bool):
    limiter = RecordingLimiter()
    app = create_admin(
        config=CoreAdminConfig(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'key.db'}",
            secret_key=SECRET,
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
            login_rate_limit_by_ip=by_ip,
        ),
        login_rate_limiter=limiter,
    )
    runtime = app.state.asterion

    async def _setup():
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                session.add(
                    User(
                        email="alice@example.com",
                        hashed_password=hash_password("hunter2-strong"),
                        is_active=True,
                    )
                )

    asyncio.run(_setup())
    return app, limiter


def test_default_keys_on_email_only(tmp_path):
    app, limiter = _build(tmp_path, by_ip=False)
    with TestClient(app, raise_server_exceptions=False) as c:
        c.post("/api/v1/auth/login", json={"email": "Alice@example.com", "password": "nope"})
    assert limiter.keys, "limiter was not consulted"
    assert all(k == "alice@example.com" for k in limiter.keys)


def test_opt_in_keys_on_email_and_ip(tmp_path):
    app, limiter = _build(tmp_path, by_ip=True)
    with TestClient(app, raise_server_exceptions=False) as c:
        c.post("/api/v1/auth/login", json={"email": "Alice@example.com", "password": "nope"})
    assert limiter.keys
    assert all(k.startswith("alice@example.com|") for k in limiter.keys)
