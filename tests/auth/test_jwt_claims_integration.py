"""Configured iss/aud through the real login + auth path (Review R8).

Proves the config → token-function wiring is consistent end to end: with
``jwt_issuer`` / ``jwt_audience`` set, ``provider.login`` mints tokens carrying
the claims and ``get_current_user`` (via the auth provider) accepts them. A
mismatch in threading would surface here as a 401 on an otherwise valid token.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion import CoreAdminConfig, create_admin
from asterion.auth.password import hash_password
from asterion.auth.tokens import decode_token
from asterion.models.base import GlobalModel
from asterion.models.user import User

SECRET = "test-jwt-claims-integration-secret"
ISSUER = "asterion"
AUDIENCE = "admin-ui"
PASSWORD = "hunter2-strong"


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'claims.db'}"
    application = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key=SECRET,
            jwt_issuer=ISSUER,
            jwt_audience=AUDIENCE,
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        )
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
    yield application
    asyncio.run(runtime.db.dispose())


def test_login_mints_claims_and_me_accepts_them(app):
    client = TestClient(app, raise_server_exceptions=False)

    login = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": PASSWORD},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]

    # The minted token actually carries the configured claims. (Decoding a
    # token that has an ``aud`` requires passing the audience, else jose
    # rejects it — which is exactly why every framework decode path threads
    # the configured claims.)
    payload = decode_token(
        token, secret_key=SECRET, algorithm="HS256", issuer=ISSUER, audience=AUDIENCE
    )
    assert payload["iss"] == ISSUER
    assert payload["aud"] == AUDIENCE

    # And the auth path accepts it.
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    assert me.json()["email"] == "alice@example.com"
