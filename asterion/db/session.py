from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class DatabaseManager:
    """App-local async database manager.

    One DatabaseManager belongs to one asterion runtime.
    It owns one AsyncEngine and one async_sessionmaker.

    PostgreSQL pool sizing is configurable via the keyword arguments
    (defaults match :class:`CoreAdminConfig`). SQLite gets WAL +
    busy_timeout pragmas so independent writer sessions wait briefly
    instead of immediately erroring with "database is locked".
    """

    def __init__(
        self,
        database_url: str,
        *,
        echo: bool = False,
        pool_size: int = 10,
        max_overflow: int = 20,
        pool_pre_ping: bool = True,
    ) -> None:
        engine_kwargs: dict = {
            "echo": echo,
            "pool_pre_ping": pool_pre_ping,
        }

        is_sqlite = database_url.startswith(("sqlite://", "sqlite+aiosqlite://"))

        if database_url.startswith(("postgresql://", "postgresql+asyncpg://")):
            engine_kwargs.update(
                {
                    "pool_size": pool_size,
                    "max_overflow": max_overflow,
                }
            )
        elif is_sqlite:
            # GlobalModel.metadata declares schema="public" for Postgres.
            # SQLite has no schemas — transparently drop the qualifier.
            engine_kwargs["execution_options"] = {"schema_translate_map": {"public": None}}

        self.engine: AsyncEngine = create_async_engine(
            database_url,
            **engine_kwargs,
        )

        if is_sqlite:

            @event.listens_for(self.engine.sync_engine, "connect")
            def _sqlite_pragmas(dbapi_connection, _connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.close()

        self.sessionmaker = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
        )

    def session(self) -> AsyncSession:
        return self.sessionmaker()

    async def dispose(self) -> None:
        await self.engine.dispose()
