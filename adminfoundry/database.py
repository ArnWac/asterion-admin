from collections.abc import AsyncGenerator
from fastapi import HTTPException, Request, status
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, AsyncEngine, create_async_engine, async_sessionmaker
from adminfoundry.settings import settings

engine = create_async_engine(settings.DATABASE_URL, echo=settings.DEBUG)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# Cache of per-tenant engines keyed by schema_name
_tenant_engines: dict[str, AsyncEngine] = {}


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


def _make_tenant_engine(schema_name: str) -> AsyncEngine:
    """Create an engine whose connections have search_path set to the tenant schema."""
    eng = create_async_engine(settings.DATABASE_URL, echo=settings.DEBUG)

    @event.listens_for(eng.sync_engine, "connect")
    def set_search_path(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute(f"SET search_path TO {schema_name}, public")
        cursor.close()

    return eng


def get_or_create_tenant_engine(schema_name: str) -> AsyncEngine:
    if schema_name not in _tenant_engines:
        if "postgresql" in settings.DATABASE_URL:
            _tenant_engines[schema_name] = _make_tenant_engine(schema_name)
        else:
            # SQLite: no schema support — reuse shared engine for fast tests
            _tenant_engines[schema_name] = engine
    return _tenant_engines[schema_name]


async def get_tenant_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Tenant-scoped DB session.  Falls back to shared DB when MULTI_TENANT=false."""
    if not settings.MULTI_TENANT:
        async for session in get_db():
            yield session
        return

    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant context required",
        )

    tenant_engine = get_or_create_tenant_engine(tenant.schema_name)
    factory = async_sessionmaker(tenant_engine, expire_on_commit=False)
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
