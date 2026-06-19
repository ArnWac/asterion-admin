"""Root user admin endpoints — list + read global users.

Superadmin-only. Hand-written response model so secret columns
(``hashed_password``, ``token_version``) never leak.

The list endpoint goes through ``runtime.providers.users.list_users``
(Roadmap 2.5) so a deployment running an external UserProvider sees
ITS users in the root panel, not just the builtin ``User`` table. The
read-by-id endpoint stays on the builtin model — fetching an arbitrary
(possibly inactive) user by id for the superadmin panel is a builtin-
root concern the provider protocol intentionally doesn't cover
(``get_by_id`` filters inactive for the auth path).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from asterion.auth.dependencies import require_superadmin
from asterion.db.dependencies import get_async_session
from asterion.models.user import User
from asterion.providers.base import AdminPrincipal, UserQuery

router = APIRouter()


class UserOut(BaseModel):
    id: str
    email: str | None = None
    full_name: str | None = None
    is_active: bool
    is_superadmin: bool

    @classmethod
    def from_orm_user(cls, user: User) -> UserOut:
        return cls(
            id=str(user.id),
            email=user.email,
            full_name=user.full_name,
            is_active=user.is_active,
            is_superadmin=user.is_superadmin,
        )

    @classmethod
    def from_principal(cls, principal: AdminPrincipal) -> UserOut:
        return cls(
            id=str(principal.id),
            email=principal.email,
            full_name=principal.display_name,
            is_active=principal.is_active,
            is_superadmin=principal.is_superadmin,
        )


class UserListResponse(BaseModel):
    items: list[UserOut]
    total: int
    limit: int
    offset: int


@router.get("/users", response_model=UserListResponse)
async def list_users(
    request: Request,
    limit: int = 100,
    offset: int = 0,
    search: str | None = None,
    _current: User = Depends(require_superadmin),
) -> UserListResponse:
    provider = request.app.state.asterion.providers.users
    list_fn = getattr(provider, "list_users", None)
    if list_fn is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="The configured UserProvider does not support listing users.",
        )

    page = await list_fn(
        UserQuery(search=search, limit=limit, offset=offset),
        request=request,
    )
    return UserListResponse(
        items=[UserOut.from_principal(p) for p in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/users/{user_id}", response_model=UserOut)
async def read_user(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    _current: User = Depends(require_superadmin),
) -> UserOut:
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )
    return UserOut.from_orm_user(user)
