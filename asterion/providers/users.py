"""Default :class:`UserProvider` backed by the framework's ``User`` model.

Loads users from the public schema. External providers that wrap a
different user store (e.g. an existing app-level ``MyAppUser`` table or
a cached Google profile) replace this entirely; the framework only sees
the neutral :class:`AdminPrincipal`.

Activeness is enforced here — ``get_by_id`` returns ``None`` for users
where ``is_active`` is False, matching the behaviour of the legacy
``get_current_user`` dependency that raises 403 on inactive accounts.
"""

from __future__ import annotations

from fastapi import Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from asterion.models.user import User
from asterion.providers.base import AdminPrincipal, Page, UserQuery
from asterion.security.validation import validate_limit_offset


def _to_admin_principal(row: User) -> AdminPrincipal:
    return AdminPrincipal(
        id=str(row.id),
        email=row.email,
        display_name=row.full_name,
        is_active=bool(row.is_active),
        is_superadmin=bool(row.is_superadmin),
    )


class BuiltinSQLAlchemyUserProvider:
    """Wraps ``SELECT * FROM users WHERE id = :id``.

    Implements :class:`asterion.providers.base.UserProvider`. Reads
    its DB engine from ``request.app.state.asterion`` so the provider
    has no construction-time framework coupling — it can be instantiated
    by external apps without injecting framework internals.

    Returns ``None`` for users that don't exist or are inactive.
    """

    async def get_by_id(
        self,
        user_id: str,
        *,
        request: Request | None = None,
    ) -> AdminPrincipal | None:
        if request is None:
            raise RuntimeError(
                "BuiltinSQLAlchemyUserProvider needs the request to reach the DB; "
                "external use should pass a UserProvider that does not require it."
            )
        runtime = request.app.state.asterion
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            row = (
                await session.execute(select(User).where(User.id == user_id))
            ).scalar_one_or_none()
        if row is None or not row.is_active:
            return None
        return _to_admin_principal(row)

    async def list_users(
        self,
        query: UserQuery,
        *,
        request: Request | None = None,
    ) -> Page:
        """List users for the root admin panel.

        Returns ALL users including inactive ones (unlike ``get_by_id``,
        which filters inactive for the auth path) — the root panel needs
        to see and re-activate disabled accounts. ``search`` matches
        email + full_name case-insensitively.
        """
        if request is None:
            raise RuntimeError(
                "BuiltinSQLAlchemyUserProvider needs the request to reach the DB; "
                "external use should pass a UserProvider that does not require it."
            )
        limit, offset = validate_limit_offset(limit=query.limit, offset=query.offset)

        base = select(User)
        if query.search:
            needle = f"%{query.search.strip()}%"
            base = base.where(or_(User.email.ilike(needle), User.full_name.ilike(needle)))

        runtime = request.app.state.asterion
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            total = (
                await session.execute(select(func.count()).select_from(base.subquery()))
            ).scalar_one()
            rows = (
                (await session.execute(base.order_by(User.email).limit(limit).offset(offset)))
                .scalars()
                .all()
            )

        return Page(
            items=[_to_admin_principal(r) for r in rows],
            total=int(total),
            limit=limit,
            offset=offset,
        )
