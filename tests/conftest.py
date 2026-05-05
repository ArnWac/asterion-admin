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
from sqlalchemy.pool import StaticPool
from coreAdmin_api.main import app
from coreAdmin_api.database import get_db
from coreAdmin_api.models.base import Base
from coreAdmin_api.models.user import User
from coreAdmin_api.models.role import Role  # noqa: F401 — register table
from coreAdmin_api.models.tenant import Tenant  # noqa: F401 — register table
from coreAdmin_api.models.audit_log import AuditLog  # noqa: F401 — register table
from coreAdmin_api.models.impersonation_log import ImpersonationLog  # noqa: F401 — register table
from coreAdmin_api.extensions.jobs.models import Job  # noqa: F401 — register table
from coreAdmin_api.models.change_request import ChangeRequest  # noqa: F401 — register table
from coreAdmin_api.auth import hash_password
from coreAdmin_api.token_blacklist import clear_blacklist

# Use bcrypt rounds=4 in tests (vs default 12) — 256× faster, still valid hashes
_orig_gensalt = _bcrypt.gensalt
_bcrypt.gensalt = lambda rounds=4, prefix=b"2b": _orig_gensalt(rounds=4, prefix=prefix)


# ---------------------------------------------------------------------------
# Session-scoped engine — created once, shared across all tests
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="session")
async def db_engine() -> AsyncEngine:
    """Single in-memory SQLite engine for the entire test session."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def session_factory(db_engine: AsyncEngine) -> async_sessionmaker:
    import coreAdmin_api.database as _db
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    # Patch direct AsyncSessionLocal usage (e.g. AuditMiddleware) so no middleware
    # tries to open a real PostgreSQL connection during tests.
    _db.AsyncSessionLocal = factory
    return factory


# ---------------------------------------------------------------------------
# Per-test cleanup — truncate all tables before each test
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
async def clean_tables(db_engine: AsyncEngine):
    """Delete all rows from every table before each test (fast, FK-safe order)."""
    async with db_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())


@pytest.fixture(autouse=True)
def reset_token_blacklist():
    """Ensure the in-memory blacklist is clean before and after every test."""
    clear_blacklist()
    yield
    clear_blacklist()


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    from coreAdmin_api.middleware.rate_limit import reset_rate_limiter
    reset_rate_limiter()
    yield
    reset_rate_limiter()


# ---------------------------------------------------------------------------
# Per-test session and HTTP client
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db(session_factory: async_sessionmaker) -> AsyncSession:
    """Test setup session — use this to create/modify fixtures directly."""
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(session_factory: async_sessionmaker, db: AsyncSession):
    """HTTP client — gets its own fresh session per request from the shared engine."""
    async def override_get_db():
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
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
