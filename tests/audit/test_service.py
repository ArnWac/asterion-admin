"""Unit tests for asterion.audit.service.

Verifies the audit writer: it persists rows in an isolated session, it
sanitizes payloads through sanitize_payload, and it never re-raises on
internal failure.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion.audit import (
    ADMIN_ACTION,
    CRUD_CREATE,
    LOGIN_SUCCESS,
    audit_payload,
    record_audit,
    record_audit_in_session,
)
from asterion.db.session import DatabaseManager
from asterion.models.audit_log import AuditLog
from asterion.models.base import GlobalModel
from asterion.models.user import User


@pytest_asyncio.fixture
async def db(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'audit.db'}"
    manager = DatabaseManager(url)
    async with manager.engine.begin() as conn:
        await conn.run_sync(GlobalModel.metadata.create_all)
    yield manager
    await manager.dispose()


async def _all_audits(db: DatabaseManager) -> list[AuditLog]:
    factory = async_sessionmaker(db.engine, expire_on_commit=False)
    async with factory() as session:
        result = await session.execute(select(AuditLog))
        return list(result.scalars().all())


@pytest.mark.asyncio
async def test_record_audit_writes_row(db):
    await record_audit(
        db,
        action=LOGIN_SUCCESS,
        method="POST",
        path="/api/v1/auth/login",
        status_code=200,
        changes={"email": "user@example.com"},
    )
    rows = await _all_audits(db)
    assert len(rows) == 1
    row = rows[0]
    assert row.action == LOGIN_SUCCESS
    assert row.method == "POST"
    assert row.path == "/api/v1/auth/login"
    assert row.status_code == 200
    assert row.changes == {"email": "user@example.com"}


@pytest.mark.asyncio
async def test_record_audit_sanitizes_changes(db):
    await record_audit(
        db,
        action=CRUD_CREATE,
        changes={
            "email": "x@y.com",
            "password": "hunter2",
            "nested": {"access_token": "ABC", "ok": True},
        },
    )
    rows = await _all_audits(db)
    assert rows[0].changes["email"] == "x@y.com"
    assert rows[0].changes["password"] == "***REDACTED***"
    assert rows[0].changes["nested"]["access_token"] == "***REDACTED***"
    assert rows[0].changes["nested"]["ok"] is True


@pytest.mark.asyncio
async def test_record_audit_captures_actor_user_id(db):
    factory = async_sessionmaker(db.engine, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            from asterion.auth.password import hash_password

            user = User(
                email="alice@example.com",
                hashed_password=hash_password("hunter2-strong"),
                is_active=True,
            )
            session.add(user)
        await session.refresh(user)

    await record_audit(db, action=LOGIN_SUCCESS, actor=user)
    rows = await _all_audits(db)
    assert rows[0].actor_user_id == user.id
    assert rows[0].actor_label == user.email


@pytest.mark.asyncio
async def test_record_audit_stringifies_record_id(db):
    rec_uuid = uuid.uuid4()
    await record_audit(
        db,
        action=ADMIN_ACTION,
        record_id=rec_uuid,
        resource="widgets",
    )
    rows = await _all_audits(db)
    assert rows[0].record_id == str(rec_uuid)
    assert rows[0].resource == "widgets"


@pytest.mark.asyncio
async def test_record_audit_omits_changes_when_none(db):
    await record_audit(db, action=ADMIN_ACTION)
    rows = await _all_audits(db)
    assert rows[0].changes is None


@pytest.mark.asyncio
async def test_record_audit_swallows_internal_errors(db, caplog):
    """If the underlying DB operation explodes, record_audit must not raise."""

    class _BrokenDb:
        engine = None  # so dispose isn't called

        def session(self):
            raise RuntimeError("database exploded")

        async def dispose(self):  # pragma: no cover - never called
            pass

    broken = _BrokenDb()
    # No exception should propagate, even though session() raises.
    await record_audit(broken, action=LOGIN_SUCCESS)


@pytest.mark.asyncio
async def test_record_audit_does_not_use_caller_session(db):
    """``record_audit`` opens its own session — the caller can pass anything,
    including a DatabaseManager whose engine has never been touched."""
    await record_audit(db, action=LOGIN_SUCCESS, changes={"x": 1})
    rows = await _all_audits(db)
    assert len(rows) == 1


# --- audit_payload ---


def test_audit_payload_sanitizes_changes():
    row = audit_payload(
        action=LOGIN_SUCCESS,
        changes={"email": "x@y.com", "password": "secret"},
    )
    assert row.changes["email"] == "x@y.com"
    assert row.changes["password"] == "***REDACTED***"


def test_audit_payload_stringifies_record_id():
    rid = uuid.uuid4()
    row = audit_payload(action=ADMIN_ACTION, record_id=rid)
    assert row.record_id == str(rid)


def test_audit_payload_omits_changes_when_none():
    row = audit_payload(action=ADMIN_ACTION)
    assert row.changes is None


def test_audit_payload_handles_external_string_actor_id():
    """1.5 / Doc-2 §3: the audit service must accept an AdminPrincipal
    with an arbitrary string id (external auth providers). UUID-shaped
    ids round-trip to ``actor_user_id``; opaque ids fall back to
    ``actor_label`` instead of raising."""
    from asterion.providers.base import AdminPrincipal

    # UUID-shaped string id → keeps in actor_user_id.
    pid = str(uuid.uuid4())
    row = audit_payload(
        action=LOGIN_SUCCESS,
        actor=AdminPrincipal(id=pid, email="a@x"),
    )
    assert row.actor_user_id == uuid.UUID(pid)
    assert row.actor_label == "a@x"

    # Opaque external id (Keycloak sub, OAuth subject) → silent
    # fallback, actor still identified via label.
    row = audit_payload(
        action=LOGIN_SUCCESS,
        actor=AdminPrincipal(id="keycloak|user-12345", email="b@y"),
    )
    assert row.actor_user_id is None
    assert row.actor_label == "b@y"


def test_audit_payload_handles_uuid_actor_id_directly():
    """Builtin path: ``AdminPrincipal.id`` can already carry a
    ``uuid.UUID`` (when the BuiltinSQLAlchemyUserProvider hydrates a
    principal). Must round-trip unchanged."""
    from asterion.providers.base import AdminPrincipal

    pid = uuid.uuid4()
    row = audit_payload(
        action=LOGIN_SUCCESS,
        actor=AdminPrincipal(id=pid, email="c@z"),  # type: ignore[arg-type]
    )
    assert row.actor_user_id == pid


# --- record_audit_in_session (savepoint isolation) ---


@pytest.mark.asyncio
async def test_record_audit_in_session_persists_with_outer_commit(db):
    factory = async_sessionmaker(db.engine, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            await record_audit_in_session(session, action=CRUD_CREATE, resource="widgets")
    rows = await _all_audits(db)
    assert len(rows) == 1
    assert rows[0].resource == "widgets"


@pytest.mark.asyncio
async def test_record_audit_in_session_swallows_errors(db):
    """If the audit row itself can't flush, the savepoint rolls back and the
    helper returns without raising."""
    factory = async_sessionmaker(db.engine, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            # Pass an invalid changes value that JSON can't serialize.
            # Pass kwargs only your version accepts; we know the helper
            # always swallows so we use an unrelated invalid attr instead.
            class _BadActor:
                @property
                def id(self):
                    raise RuntimeError("attribute boom")

                @property
                def email(self):
                    raise RuntimeError("attribute boom")

            await record_audit_in_session(session, action=CRUD_CREATE, actor=_BadActor())
        # Outer commit still succeeds because the savepoint swallowed.
    rows = await _all_audits(db)
    assert rows == []  # No audit row written
