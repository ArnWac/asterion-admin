import os

import bcrypt as _bcrypt
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy import text
from sqlalchemy.pool import StaticPool, NullPool
# The whole test suite reuses the multi-tenant example app — it already wires
# jobs + workflows extensions and is the most realistic mounted-everything app.
# TODO: extract a test-owned app_factory fixture so tests don't depend on examples.
from examples.basic_multi.app import app
from adminfoundry.database import get_db, get_admin_db
from adminfoundry.models.base import Base
from adminfoundry.models.user import User
from adminfoundry.models.role import Role  # noqa: F401 — register table
from adminfoundry.models.tenant import Tenant  # noqa: F401 — register table
from adminfoundry.models.audit_log import AuditLog  # noqa: F401 — register table
from adminfoundry.models.impersonation_log import ImpersonationLog  # noqa: F401 — register table
from adminfoundry.extensions.jobs.models import Job  # noqa: F401 — register table
from adminfoundry.extensions.workflows.models import ChangeRequest  # noqa: F401 — register table
from adminfoundry.extensions.webhooks.models import WebhookSubscription, WebhookDelivery  # noqa: F401 — register table
from adminfoundry.models.revoked_token import RevokedToken  # noqa: F401 — register table
from adminfoundry.models.password_reset_token import PasswordResetToken  # noqa: F401 — register table
from adminfoundry.models.role_permission import RolePermission  # noqa: F401 — register table
from adminfoundry.models.tenant_membership import TenantMembership  # noqa: F401 — register table
from adminfoundry.auth import hash_password
from adminfoundry.token_blacklist import clear_blacklist

# Use bcrypt rounds=4 in tests (vs default 12) — 256× faster, still valid hashes
_orig_gensalt = _bcrypt.gensalt
_bcrypt.gensalt = lambda rounds=4, prefix=b"2b": _orig_gensalt(rounds=4, prefix=prefix)

# ---------------------------------------------------------------------------
# Database URL — set TEST_DATABASE_URL to run against PostgreSQL:
#   $env:TEST_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/adminfoundry_test"
#   pytest tests/
# ---------------------------------------------------------------------------

_TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "sqlite+aiosqlite:///:memory:",
)
_IS_POSTGRES = "postgresql" in _TEST_DB_URL


# ---------------------------------------------------------------------------
# Session-scoped engine — created once, shared across all tests
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="session")
async def db_engine() -> AsyncEngine:
    if _IS_POSTGRES:
        engine = create_async_engine(_TEST_DB_URL, poolclass=NullPool)
    else:
        engine = create_async_engine(
            _TEST_DB_URL,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def session_factory(db_engine: AsyncEngine) -> async_sessionmaker:
    import adminfoundry.database as _db
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    # Patch AsyncSessionLocal so middleware (AuditMiddleware, TenantMiddleware)
    # doesn't open a separate real-DB connection during tests.
    _db.AsyncSessionLocal = factory
    return factory


# ---------------------------------------------------------------------------
# Per-test cleanup — truncate all tables before each test
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
async def clean_tables(db_engine: AsyncEngine):
    if _IS_POSTGRES:
        # TRUNCATE CASCADE is atomic and respects FK order automatically.
        table_names = ", ".join(
            f'"{t.name}"' for t in Base.metadata.sorted_tables
        )
        async with db_engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))
    else:
        async with db_engine.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                await conn.execute(table.delete())


@pytest.fixture(autouse=True)
def reset_token_blacklist():
    clear_blacklist()
    yield
    clear_blacklist()


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    from adminfoundry.middleware.rate_limit import reset_rate_limiter
    reset_rate_limiter()
    yield
    reset_rate_limiter()


@pytest.fixture(autouse=True)
def reset_tenant_cache():
    from adminfoundry.tenancy.resolver import clear_tenant_cache
    clear_tenant_cache()
    yield
    clear_tenant_cache()


# ---------------------------------------------------------------------------
# Per-test session and HTTP client
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db(session_factory: async_sessionmaker) -> AsyncSession:
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(session_factory: async_sessionmaker, db: AsyncSession):
    async def override_get_db():
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_admin_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Shared user fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def superadmin(db: AsyncSession) -> User:
    user = User(
        email="admin@example.com",
        hashed_password=hash_password("password123"),
        full_name="Admin User",
        is_active=True,
        is_superadmin=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@pytest_asyncio.fixture
async def inactive_user(db: AsyncSession) -> User:
    user = User(
        email="inactive@example.com",
        hashed_password=hash_password("password123"),
        is_active=False,
        is_superadmin=False,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user
