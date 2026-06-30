"""G3 — audit retention (public-table pruning + cutoff). SQLite side.

The per-tenant schema sweep is a PostgreSQL operation (see
``tests/postgres/test_audit_retention.py``); here we cover the cutoff maths and
the public-table prune that also runs on SQLite.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion.auth.password import hash_password
from asterion.db.session import DatabaseManager
from asterion.models.audit_log import AuditLog
from asterion.models.base import GlobalModel
from asterion.models.user import User
from asterion.privacy.anonymizer import anonymized_email
from asterion.privacy.retention import apply_retention, retention_cutoff


def test_retention_cutoff_is_now_minus_days():
    now = datetime(2026, 6, 30, tzinfo=UTC)
    assert retention_cutoff(90, now=now) == now - timedelta(days=90)


@pytest.fixture
async def db(tmp_path):
    manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'retention.db'}")
    async with manager.engine.begin() as conn:
        await conn.run_sync(GlobalModel.metadata.create_all)
    yield manager
    await manager.dispose()


async def _seed_audit(db: DatabaseManager, *, days_old: int) -> None:
    created = datetime.now(UTC) - timedelta(days=days_old)
    factory = async_sessionmaker(db.engine, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            session.add(
                AuditLog(
                    method="POST",
                    path="/x",
                    status_code=200,
                    action="probe",
                    created_at=created,
                    updated_at=created,
                )
            )


async def _count(db: DatabaseManager) -> int:
    factory = async_sessionmaker(db.engine, expire_on_commit=False)
    async with factory() as session:
        return len((await session.execute(select(AuditLog))).scalars().all())


async def test_apply_retention_prunes_public_older_than_cutoff(db):
    await _seed_audit(db, days_old=120)
    await _seed_audit(db, days_old=10)

    results = await apply_retention(db, retention_days=90, all_tenants=False)

    assert results == {"public": 1}
    assert await _count(db) == 1  # only the recent row survives


async def test_apply_retention_all_tenants_on_sqlite_is_public_only(db):
    # No tenant schemas on SQLite — all_tenants must degrade to public-only
    # without error and without spurious tenant keys.
    await _seed_audit(db, days_old=120)
    results = await apply_retention(db, retention_days=90, all_tenants=True)
    assert results == {"public": 1}


async def _seed_user(db, *, email, days_deactivated=None, active=True):
    from datetime import UTC, datetime, timedelta

    factory = async_sessionmaker(db.engine, expire_on_commit=False)
    deactivated_at = (
        None if days_deactivated is None else datetime.now(UTC) - timedelta(days=days_deactivated)
    )
    async with factory() as session:
        async with session.begin():
            user = User(
                email=email,
                hashed_password=hash_password("hunter2-strong"),
                full_name="Seed",
                is_active=active,
                deactivated_at=deactivated_at,
            )
            session.add(user)
        await session.refresh(user)
        return user.id


async def _user_by_id(db, uid):
    factory = async_sessionmaker(db.engine, expire_on_commit=False)
    async with factory() as session:
        return await session.get(User, uid)


async def test_auto_anonymize_only_past_sperrfrist(db):
    long_gone = await _seed_user(db, email="gone@example.com", days_deactivated=200, active=False)
    recent = await _seed_user(db, email="recent@example.com", days_deactivated=10, active=False)
    active = await _seed_user(db, email="active@example.com", active=True)

    results = await apply_retention(
        db, retention_days=90, all_tenants=False, user_anonymize_after_days=180
    )
    assert results["anonymized_users"] == 1

    # Past the sperrfrist → anonymised.
    gone_user = await _user_by_id(db, long_gone)
    assert gone_user.email == anonymized_email(long_gone)
    assert gone_user.full_name is None
    # Still within the sperrfrist / still active → untouched.
    assert (await _user_by_id(db, recent)).email == "recent@example.com"
    assert (await _user_by_id(db, active)).email == "active@example.com"


async def test_auto_anonymize_is_idempotent(db):
    await _seed_user(db, email="gone@example.com", days_deactivated=200, active=False)
    first = await apply_retention(
        db, retention_days=90, all_tenants=False, user_anonymize_after_days=180
    )
    assert first["anonymized_users"] == 1
    # Second run finds nothing new (the tombstone email excludes it).
    second = await apply_retention(
        db, retention_days=90, all_tenants=False, user_anonymize_after_days=180
    )
    assert second["anonymized_users"] == 0


async def test_no_anonymization_when_unset(db):
    await _seed_user(db, email="gone@example.com", days_deactivated=200, active=False)
    results = await apply_retention(db, retention_days=90, all_tenants=False)
    assert "anonymized_users" not in results
    # User is left alone.
    factory = async_sessionmaker(db.engine, expire_on_commit=False)
    async with factory() as session:
        rows = (await session.execute(select(User))).scalars().all()
    assert rows[0].email == "gone@example.com"
