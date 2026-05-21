"""Default :class:`UserProvider` backed by the framework's ``User`` model.

Loads users from the public schema. External providers that wrap a
different user store (e.g. an existing app-level ``MyAppUser`` table or
a cached Google profile) replace this entirely; the framework only sees
the neutral :class:`AdminUser`.

Activeness is enforced here — ``get_by_id`` returns ``None`` for users
where ``is_active`` is False, matching the behaviour of the legacy
``get_current_user`` dependency that raises 403 on inactive accounts.
"""

from __future__ import annotations

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from adminfoundry.models.user import User
from adminfoundry.providers.base import AdminUser


def _to_admin_user(row: User) -> AdminUser:
    return AdminUser(
        id=str(row.id),
        email=row.email,
        display_name=row.full_name,
        is_active=bool(row.is_active),
        is_superadmin=bool(row.is_superadmin),
    )


class BuiltinSQLAlchemyUserProvider:
    """Wraps ``SELECT * FROM users WHERE id = :id``.

    Implements :class:`adminfoundry.providers.base.UserProvider`. Reads
    its DB engine from ``request.app.state.adminfoundry`` so the provider
    has no construction-time framework coupling — it can be instantiated
    by external apps without injecting framework internals.

    Returns ``None`` for users that don't exist or are inactive.
    """

    async def get_by_id(
        self,
        user_id: str,
        *,
        request: Request | None = None,
    ) -> AdminUser | None:
        if request is None:
            raise RuntimeError(
                "BuiltinSQLAlchemyUserProvider needs the request to reach the DB; "
                "external use should pass a UserProvider that does not require it."
            )
        runtime = request.app.state.adminfoundry
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            row = (
                await session.execute(select(User).where(User.id == user_id))
            ).scalar_one_or_none()
        if row is None or not row.is_active:
            return None
        return _to_admin_user(row)
