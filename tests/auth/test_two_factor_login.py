"""2FA login step-up (Roadmap 3.4b).

After ``/auth/login`` validates the password for a user with
``totp_enabled=True``, it returns a short-lived MFA challenge instead
of access+refresh. ``POST /auth/2fa/login`` exchanges the challenge +
a code (TOTP or backup) for the real token pair.
"""

from __future__ import annotations

import asyncio

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion import CoreAdminConfig, create_admin
from asterion.auth.password import hash_password
from asterion.auth.tokens import (
    create_access_token,
    create_mfa_challenge_token,
    decode_token,
)
from asterion.models.base import GlobalModel
from asterion.models.user import User

SECRET = "test-2fa-login-secret"
ALG = "HS256"
PASSWORD = "hunter2-strong"


def _enroll_user(runtime, email: str, *, enabled: bool):
    """Build a user that already has 2FA enrolled (and optionally
    enabled). Returns (user_id, totp_secret)."""
    totp_secret = pyotp.random_base32()

    async def _go():
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                u = User(
                    email=email,
                    hashed_password=hash_password(PASSWORD),
                    is_active=True,
                    token_version=0,
                    totp_secret=totp_secret,
                    totp_enabled=enabled,
                )
                session.add(u)
            await session.refresh(u)
            return u.id

    user_id = asyncio.run(_go())
    return user_id, totp_secret


def _build_app(tmp_path, *, db_name: str):
    db_url = f"sqlite+aiosqlite:///{tmp_path / db_name}"
    return create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key=SECRET,
            app_title="TestAdmin",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        )
    )


def _client(app):
    return TestClient(app, raise_server_exceptions=False)


def _login(app, email: str = "alice@example.com"):
    return _client(app).post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})


# ---------------------------------------------------------------------------
# /auth/login step-up — 2FA user gets a challenge, non-2FA user keeps token pair
# ---------------------------------------------------------------------------


@pytest.fixture
def app_with_2fa_user(tmp_path):
    app = _build_app(tmp_path, db_name="2fa_login.db")
    runtime = app.state.asterion
    user_id, secret = _enroll_user(runtime, "alice@example.com", enabled=True)
    yield app, runtime, user_id, secret
    asyncio.run(runtime.db.dispose())


def test_login_for_2fa_user_returns_challenge_not_tokens(app_with_2fa_user):
    app, _, _, _ = app_with_2fa_user
    resp = _login(app)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mfa_required"] is True
    assert body["mfa_token"]
    assert body["access_token"] is None
    assert body["refresh_token"] is None


def test_challenge_token_has_correct_type(app_with_2fa_user):
    app, _, _, _ = app_with_2fa_user
    body = _login(app).json()
    payload = decode_token(body["mfa_token"], secret_key=SECRET, algorithm=ALG)
    assert payload["type"] == "mfa_challenge"


def test_challenge_does_not_authenticate_other_routes(app_with_2fa_user):
    """The challenge is NOT a real access token — it must be rejected
    on protected routes like /auth/me."""
    app, _, _, _ = app_with_2fa_user
    body = _login(app).json()
    resp = _client(app).get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {body['mfa_token']}"},
    )
    assert resp.status_code == 401


def test_login_for_non_2fa_user_returns_token_pair_unchanged(tmp_path):
    """Regression guard: a user without 2FA still gets access+refresh
    directly. The step-up branch must not affect them."""
    app = _build_app(tmp_path, db_name="non2fa.db")
    runtime = app.state.asterion
    _enroll_user(runtime, "bob@example.com", enabled=False)
    body = (
        _client(app)
        .post(
            "/api/v1/auth/login",
            json={"email": "bob@example.com", "password": PASSWORD},
        )
        .json()
    )
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["mfa_required"] is False
    assert body["mfa_token"] is None
    asyncio.run(runtime.db.dispose())


# ---------------------------------------------------------------------------
# /auth/2fa/login — exchange challenge + code for token pair
# ---------------------------------------------------------------------------


def test_2fa_login_with_correct_totp_returns_token_pair(app_with_2fa_user):
    app, _, _, secret = app_with_2fa_user
    body = _login(app).json()
    code = pyotp.TOTP(secret).now()
    resp = _client(app).post(
        "/api/v1/auth/2fa/login",
        json={"mfa_token": body["mfa_token"], "code": code},
    )
    assert resp.status_code == 200, resp.text
    new = resp.json()
    assert new["access_token"]
    assert new["refresh_token"]
    assert new["mfa_required"] is False


def test_2fa_login_minted_access_token_authenticates(app_with_2fa_user):
    app, _, _, secret = app_with_2fa_user
    body = _login(app).json()
    code = pyotp.TOTP(secret).now()
    new = (
        _client(app)
        .post(
            "/api/v1/auth/2fa/login",
            json={"mfa_token": body["mfa_token"], "code": code},
        )
        .json()
    )
    me = _client(app).get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {new['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["email"] == "alice@example.com"


def test_2fa_login_rejects_wrong_code(app_with_2fa_user):
    app, _, _, _ = app_with_2fa_user
    body = _login(app).json()
    resp = _client(app).post(
        "/api/v1/auth/2fa/login",
        json={"mfa_token": body["mfa_token"], "code": "000000"},
    )
    assert resp.status_code == 401


def test_challenge_is_single_use_after_success(app_with_2fa_user):
    """Replaying the same challenge after a successful exchange must
    401 — challenge is revoked on consume."""
    app, _, _, secret = app_with_2fa_user
    body = _login(app).json()
    code = pyotp.TOTP(secret).now()
    first = _client(app).post(
        "/api/v1/auth/2fa/login",
        json={"mfa_token": body["mfa_token"], "code": code},
    )
    assert first.status_code == 200
    replay = _client(app).post(
        "/api/v1/auth/2fa/login",
        json={"mfa_token": body["mfa_token"], "code": code},
    )
    assert replay.status_code == 401


def test_2fa_login_rejects_access_token_as_challenge(app_with_2fa_user):
    """An access token presented as mfa_token must be rejected (only
    type=mfa_challenge is accepted)."""
    app, _, user_id, _ = app_with_2fa_user
    access = create_access_token(
        user_id,
        secret_key=SECRET,
        algorithm=ALG,
        expires_minutes=10,
        token_version=0,
    )
    resp = _client(app).post(
        "/api/v1/auth/2fa/login",
        json={"mfa_token": access, "code": "123456"},
    )
    assert resp.status_code == 401


def test_2fa_login_requires_exactly_one_of_code_or_backup(app_with_2fa_user):
    app, _, _, _ = app_with_2fa_user
    body = _login(app).json()
    # neither
    resp = _client(app).post("/api/v1/auth/2fa/login", json={"mfa_token": body["mfa_token"]})
    assert resp.status_code == 400
    # both
    resp = _client(app).post(
        "/api/v1/auth/2fa/login",
        json={
            "mfa_token": body["mfa_token"],
            "code": "123456",
            "backup_code": "abcd-1234",
        },
    )
    assert resp.status_code == 400


def test_challenge_rejected_after_logout_all(app_with_2fa_user):
    """A challenge issued before /logout-all bumps token_version must
    fail at the 2FA step — same tkv invariant as access/refresh."""
    app, _, user_id, secret = app_with_2fa_user
    body = _login(app).json()

    # First do a real login to get an access token, then logout-all.
    code = pyotp.TOTP(secret).now()
    pair = (
        _client(app)
        .post(
            "/api/v1/auth/2fa/login",
            json={"mfa_token": body["mfa_token"], "code": code},
        )
        .json()
    )
    _client(app).post(
        "/api/v1/auth/logout-all",
        headers={"Authorization": f"Bearer {pair['access_token']}"},
    )

    # Issue a NEW challenge (manually with the now-stale tkv).
    stale_challenge = create_mfa_challenge_token(
        user_id, secret_key=SECRET, algorithm=ALG, token_version=0
    )
    resp = _client(app).post(
        "/api/v1/auth/2fa/login",
        json={"mfa_token": stale_challenge, "code": pyotp.TOTP(secret).now()},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Second-factor brute-force throttling (Review R18)
# ---------------------------------------------------------------------------


def test_2fa_login_throttles_repeated_wrong_codes(app_with_2fa_user):
    """After the failure threshold, /2fa/login returns 429 — even for a
    correct code. Without this an attacker who cleared the password factor
    could brute-force the 6-digit TOTP within the challenge TTL."""
    app, _, _, secret = app_with_2fa_user
    token = _login(app).json()["mfa_token"]

    # A wrong code does NOT consume the challenge, so the same token can be
    # replayed — exactly the brute-force surface we are throttling. Five
    # failures (the in-memory default) trip the limiter.
    for _ in range(5):
        r = _client(app).post(
            "/api/v1/auth/2fa/login",
            json={"mfa_token": token, "code": "000000"},
        )
        assert r.status_code == 401, r.text

    # The next attempt is throttled before the code is even checked: a
    # CORRECT code is now rejected with 429.
    good = pyotp.TOTP(secret).now()
    r = _client(app).post(
        "/api/v1/auth/2fa/login",
        json={"mfa_token": token, "code": good},
    )
    assert r.status_code == 429, r.text


def test_2fa_throttle_keyed_per_user_not_per_challenge(app_with_2fa_user):
    """Re-login mints a fresh challenge, but the throttle is keyed on the
    user — so the attacker cannot reset the counter by getting a new
    challenge token."""
    app, _, _, secret = app_with_2fa_user

    token = _login(app).json()["mfa_token"]
    for _ in range(5):
        _client(app).post(
            "/api/v1/auth/2fa/login",
            json={"mfa_token": token, "code": "000000"},
        )

    # Brand-new challenge for the same user — still throttled.
    fresh = _login(app).json()["mfa_token"]
    good = pyotp.TOTP(secret).now()
    r = _client(app).post(
        "/api/v1/auth/2fa/login",
        json={"mfa_token": fresh, "code": good},
    )
    assert r.status_code == 429, r.text


def test_2fa_login_success_under_threshold_clears_counter(app_with_2fa_user):
    """A handful of failures below the limit must not lock out a legitimate
    user, and a successful exchange resets the counter."""
    app, _, _, secret = app_with_2fa_user
    token = _login(app).json()["mfa_token"]

    for _ in range(3):
        r = _client(app).post(
            "/api/v1/auth/2fa/login",
            json={"mfa_token": token, "code": "000000"},
        )
        assert r.status_code == 401, r.text

    good = pyotp.TOTP(secret).now()
    r = _client(app).post(
        "/api/v1/auth/2fa/login",
        json={"mfa_token": token, "code": good},
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Backup codes work for login
# ---------------------------------------------------------------------------


def test_2fa_login_accepts_backup_code(tmp_path):
    """A user enrolls 2FA via the regular flow (to get real backup
    codes), then uses one at /auth/2fa/login. The code is single-use."""
    app = _build_app(tmp_path, db_name="backup.db")
    runtime = app.state.asterion

    async def _setup():
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                u = User(
                    email="alice@example.com",
                    hashed_password=hash_password(PASSWORD),
                    is_active=True,
                )
                session.add(u)
            await session.refresh(u)
            return u

    asyncio.run(_setup())

    # Log in (no 2FA yet) → access token.
    body = _login(app).json()
    access = body["access_token"]
    headers = {"Authorization": f"Bearer {access}"}

    # Enroll + enable 2FA via the real flow so backup codes are minted.
    secret = _client(app).post("/api/v1/auth/2fa/setup", headers=headers).json()["secret"]
    code = pyotp.TOTP(secret).now()
    backup_codes = (
        _client(app)
        .post("/api/v1/auth/2fa/enable", headers=headers, json={"code": code})
        .json()["backup_codes"]
    )

    # Re-login → challenge now required (totp_enabled=True).
    body = _login(app).json()
    assert body["mfa_required"] is True

    # Use ONE backup code → success.
    used = backup_codes[0]
    resp = _client(app).post(
        "/api/v1/auth/2fa/login",
        json={"mfa_token": body["mfa_token"], "backup_code": used},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["access_token"]

    # Re-login + same backup code → must fail (single-use).
    body2 = _login(app).json()
    resp2 = _client(app).post(
        "/api/v1/auth/2fa/login",
        json={"mfa_token": body2["mfa_token"], "backup_code": used},
    )
    assert resp2.status_code == 401

    # A different unused backup code still works.
    resp3 = _client(app).post(
        "/api/v1/auth/2fa/login",
        json={"mfa_token": body2["mfa_token"], "backup_code": backup_codes[1]},
    )
    assert resp3.status_code == 200

    asyncio.run(runtime.db.dispose())
