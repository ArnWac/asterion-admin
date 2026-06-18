"""Login account-enumeration resistance (Review R15).

An unknown email and a wrong password must be indistinguishable to a caller
without valid credentials: same HTTP status, same body, and (via a dummy
bcrypt verify on the unknown-email branch) comparable timing. "Inactive user"
is only revealed once the *correct* password is supplied, which an enumerating
attacker does not have.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from adminfoundry import CoreAdminConfig, create_admin
from adminfoundry.auth.password import dummy_verify_password, hash_password
from adminfoundry.models.base import GlobalModel
from adminfoundry.models.user import User

SECRET = "test-enum-secret"
PASSWORD = "hunter2-strong"


@pytest.fixture
def client(tmp_path):
    app = create_admin(
        config=CoreAdminConfig(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'enum.db'}",
            secret_key=SECRET,
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        )
    )
    runtime = app.state.adminfoundry

    async def _setup():
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                session.add(
                    User(
                        email="known@example.com",
                        hashed_password=hash_password(PASSWORD),
                        is_active=True,
                    )
                )
                session.add(
                    User(
                        email="inactive@example.com",
                        hashed_password=hash_password(PASSWORD),
                        is_active=False,
                    )
                )

    asyncio.run(_setup())
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    asyncio.run(runtime.db.dispose())


def _login(client, email, password):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def test_unknown_email_and_wrong_password_are_indistinguishable(client):
    unknown = _login(client, "nobody@example.com", "whatever-pw")
    wrong = _login(client, "known@example.com", "wrong-pw")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["error"]["code"] == wrong.json()["error"]["code"]
    assert unknown.json()["error"]["message"] == wrong.json()["error"]["message"]


def test_inactive_only_revealed_with_correct_password(client):
    # Wrong password against an inactive account looks like any other failure.
    assert _login(client, "inactive@example.com", "wrong-pw").status_code == 401
    # The "inactive" signal requires the correct password (which an enumerating
    # attacker does not have).
    assert _login(client, "inactive@example.com", PASSWORD).status_code == 403


def test_dummy_verify_password_is_constant_false():
    assert dummy_verify_password("anything") is False
    assert dummy_verify_password("") is False
