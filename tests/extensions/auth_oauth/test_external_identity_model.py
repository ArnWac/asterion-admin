"""Tests for the ExternalIdentity model — Phase 8b.2.

The model itself is small, but several invariants need to hold for the
real OAuth flow (Phase 8b.7) to be safe:

* ``(provider, provider_subject)`` is UNIQUE — the database is the
  last line of defence against credential confusion.
* ``user_id`` is a CASCADE foreign key to ``users.id`` — deleting a
  user takes their identities with them, never leaves orphans.
* The model registers via ``OAuthExtension.register_models`` so it
  reaches ``GlobalBase.metadata`` and ``create_all`` sees it.

These are real-DB integration tests against the SQLite fixture every
other test uses. The Phase-8b.7 redirect flow tests live next door.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from adminfoundry import CoreAdminConfig, create_admin
from adminfoundry.auth.password import hash_password
from adminfoundry.extensions.auth_oauth import (
    ExternalIdentity,
    GoogleOIDCProvider,
    OAuthExtension,
)
from adminfoundry.models.base import GlobalModel
from adminfoundry.models.user import User
from adminfoundry.security.protected_fields import reset_for_tests as reset_protected


@pytest.fixture(autouse=True)
def _isolate_protected_fields():
    reset_protected()
    yield
    reset_protected()


@pytest.fixture
def app(tmp_path):
    app = create_admin(
        config=CoreAdminConfig(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'eid.db'}",
            secret_key="test-eid",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        ),
        extensions=[
            OAuthExtension(
                providers=[GoogleOIDCProvider(client_id="x", client_secret="y")]
            )
        ],
    )

    runtime = app.state.adminfoundry

    async def _setup():
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)

    asyncio.run(_setup())
    yield app
    asyncio.run(runtime.db.dispose())


# --- registration ---


def test_extension_registers_external_identity_model(app):
    """register_models returns the class so runtime tooling can find it."""
    runtime = app.state.adminfoundry
    assert ExternalIdentity in runtime.extension_models


def test_table_is_attached_to_shared_metadata(app):
    """The whole point of register_models: create_all sees the table."""
    del app  # fixture only needed to trigger create_admin
    assert "public.external_identities" in GlobalModel.metadata.tables


def test_create_all_actually_creates_the_table(app):
    """The Phase-8b.7 flow assumes the table exists at runtime — confirm
    create_all materialized it on the engine."""
    runtime = app.state.adminfoundry

    async def _probe() -> set[str]:
        async with runtime.db.engine.connect() as conn:
            # SQLite-friendly introspection. PostgreSQL would use
            # information_schema; SQLite uses sqlite_master.
            from sqlalchemy import text

            result = await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
            return {row[0] for row in result}

    tables = asyncio.run(_probe())
    assert "external_identities" in tables


# --- column shape ---


def test_unique_constraint_on_provider_plus_subject(app):
    """Two rows with the same (provider, provider_subject) MUST fail —
    that's the credential-confusion guard."""
    runtime = app.state.adminfoundry
    factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)

    async def _run():
        async with factory() as s, s.begin():
            u1 = User(
                email="alice@example.com",
                hashed_password=hash_password("hunter2-strong"),
                is_active=True,
            )
            u2 = User(
                email="bob@example.com",
                hashed_password=hash_password("hunter2-strong"),
                is_active=True,
            )
            s.add_all([u1, u2])
            await s.flush()
            s.add(
                ExternalIdentity(
                    provider="google",
                    provider_subject="123",
                    user_id=u1.id,
                )
            )
            await s.flush()
            s.add(
                ExternalIdentity(
                    provider="google",
                    provider_subject="123",  # same subject!
                    user_id=u2.id,
                )
            )
            with pytest.raises(IntegrityError):
                await s.flush()

    asyncio.run(_run())


def test_same_subject_allowed_across_different_providers(app):
    """The unique constraint is (provider, subject), not just subject —
    a Google sub and a GitHub sub can collide on the string '123'."""
    runtime = app.state.adminfoundry
    factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)

    async def _run() -> int:
        async with factory() as s, s.begin():
            u = User(
                email="carol@example.com",
                hashed_password=hash_password("hunter2-strong"),
                is_active=True,
            )
            s.add(u)
            await s.flush()
            s.add(ExternalIdentity(provider="google", provider_subject="123", user_id=u.id))
            s.add(ExternalIdentity(provider="github", provider_subject="123", user_id=u.id))
        async with factory() as s:
            rows = (await s.execute(select(ExternalIdentity))).scalars().all()
            return len(rows)

    assert asyncio.run(_run()) == 2


def test_one_user_can_link_multiple_providers(app):
    """user_id is indexed but NOT unique — multiple identities per user OK."""
    runtime = app.state.adminfoundry
    factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)

    async def _run() -> list[str]:
        async with factory() as s, s.begin():
            u = User(
                email="dan@example.com",
                hashed_password=hash_password("hunter2-strong"),
                is_active=True,
            )
            s.add(u)
            await s.flush()
            s.add_all([
                ExternalIdentity(provider="google", provider_subject="g1", user_id=u.id),
                ExternalIdentity(provider="google", provider_subject="g2", user_id=u.id),
                ExternalIdentity(provider="github", provider_subject="h1", user_id=u.id),
            ])
        async with factory() as s:
            rows = (
                await s.execute(
                    select(ExternalIdentity).where(ExternalIdentity.user_id == u.id)
                )
            ).scalars().all()
            return sorted(f"{r.provider}:{r.provider_subject}" for r in rows)

    assert asyncio.run(_run()) == ["github:h1", "google:g1", "google:g2"]


def test_optional_fields_default_to_none(app):
    """email_at_provider / name / picture_url / hosted_domain are nullable."""
    runtime = app.state.adminfoundry
    factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)

    async def _run() -> ExternalIdentity:
        async with factory() as s, s.begin():
            u = User(
                email="frank@example.com",
                hashed_password=hash_password("hunter2-strong"),
                is_active=True,
            )
            s.add(u)
            await s.flush()
            row = ExternalIdentity(
                provider="google",
                provider_subject=str(uuid.uuid4()),
                user_id=u.id,
            )
            s.add(row)
        async with factory() as s:
            return (
                await s.execute(
                    select(ExternalIdentity).where(ExternalIdentity.user_id == u.id)
                )
            ).scalar_one()

    out = asyncio.run(_run())
    assert out.email_at_provider is None
    assert out.name is None
    assert out.picture_url is None
    assert out.hosted_domain is None
