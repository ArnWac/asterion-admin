"""G8 — data-subject export + DSAR log."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion.auth.password import hash_password
from asterion.db.session import DatabaseManager
from asterion.models.audit_log import AuditLog
from asterion.models.base import GlobalModel
from asterion.models.saved_filter import SavedFilter
from asterion.models.tenant import Tenant
from asterion.models.tenant_membership import TenantMembership
from asterion.models.user import User
from asterion.privacy.export import (
    SubjectNotFoundError,
    export_subject,
    list_subject_requests,
    record_subject_request,
)


@pytest.fixture
async def db(tmp_path):
    manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'export.db'}")
    async with manager.engine.begin() as conn:
        await conn.run_sync(GlobalModel.metadata.create_all)
    yield manager
    await manager.dispose()


async def _seed_subject(db: DatabaseManager) -> uuid.UUID:
    factory = async_sessionmaker(db.engine, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            user = User(
                email="subject@example.com",
                hashed_password=hash_password("hunter2-strong"),
                full_name="Subject Person",
                totp_secret="SECRETBASE32",
            )
            tenant = Tenant(name="Acme", slug="acme", schema_name="tenant_acme")
            session.add_all([user, tenant])
            await session.flush()
            uid = user.id
            session.add_all(
                [
                    TenantMembership(user_id=uid, tenant_id=tenant.id),
                    AuditLog(
                        method="POST",
                        path="/x",
                        status_code=200,
                        action="crud_update",
                        actor_user_id=uid,
                        actor_label="subject@example.com",
                    ),
                    SavedFilter(
                        user_id=str(uid),
                        tenant_id=str(tenant.id),
                        resource="posts",
                        name="mine",
                        payload={},
                    ),
                ]
            )
    return uid


async def test_export_bundle_includes_all_public_sources(db):
    uid = await _seed_subject(db)

    bundle = await export_subject(db, uid)

    assert bundle["subject"]["email"] == "subject@example.com"
    assert len(bundle["memberships"]) == 1
    assert len(bundle["audit_actions"]) == 1
    assert len(bundle["saved_filters"]) == 1
    assert bundle["scope"] == "public"


async def test_export_drops_secret_fields(db):
    uid = await _seed_subject(db)
    bundle = await export_subject(db, uid)
    # Secrets must never appear in a subject export — even though it's the
    # subject's own data, returning the password hash / TOTP secret is a risk.
    assert "hashed_password" not in bundle["subject"]
    assert "totp_secret" not in bundle["subject"]
    # The subject's actual PII *is* returned (that's the point of Art. 15).
    assert bundle["subject"]["full_name"] == "Subject Person"


async def test_export_unknown_subject_raises(db):
    with pytest.raises(SubjectNotFoundError):
        await export_subject(db, uuid.uuid4())


async def test_record_and_list_dsar(db):
    uid = await _seed_subject(db)

    row = await record_subject_request(
        db, subject_user_id=uid, request_type="erasure", note="ticket-42"
    )
    assert row["request_type"] == "erasure"
    assert row["status"] == "received"
    assert row["note"] == "ticket-42"

    rows = await list_subject_requests(db, uid)
    assert len(rows) == 1
    assert rows[0]["subject_user_id"] == str(uid)


async def test_dsar_appears_in_export(db):
    uid = await _seed_subject(db)
    await record_subject_request(db, subject_user_id=uid, request_type="access", status="completed")
    bundle = await export_subject(db, uid)
    assert len(bundle["data_subject_requests"]) == 1


async def test_record_rejects_unknown_type(db):
    uid = await _seed_subject(db)
    with pytest.raises(ValueError, match="Unknown request_type"):
        await record_subject_request(db, subject_user_id=uid, request_type="bogus")  # type: ignore[arg-type]


async def test_record_rejects_unknown_status(db):
    uid = await _seed_subject(db)
    with pytest.raises(ValueError, match="Unknown status"):
        await record_subject_request(
            db,
            subject_user_id=uid,
            request_type="access",
            status="weird",  # type: ignore[arg-type]
        )
