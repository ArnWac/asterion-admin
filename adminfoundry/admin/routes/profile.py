"""Admin profile endpoints — current user self-service."""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from adminfoundry.database import get_db
from adminfoundry.dependencies import get_current_user
from adminfoundry.models.user import User

router = APIRouter()


@router.get("/profile")
async def get_profile(
    current_user: User = Depends(get_current_user),
):
    """Return the current user's own profile."""
    from adminfoundry.schemas.user import UserPublic
    return UserPublic.model_validate(current_user)


@router.patch("/profile")
async def update_profile(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update name, email, or password for the current user."""
    from adminfoundry.auth import hash_password, verify_password
    from adminfoundry.schemas.user import ProfileUpdate, UserPublic

    body = ProfileUpdate(**await request.json())

    if body.new_password is not None or body.current_password is not None:
        if not body.current_password or not body.new_password:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Both current_password and new_password are required")
        if not verify_password(body.current_password, current_user.hashed_password):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Current password is incorrect")
        current_user.hashed_password = hash_password(body.new_password)

    if body.email is not None and body.email != current_user.email:
        conflict = (await db.execute(
            select(User).where(User.email == body.email, User.id != current_user.id)
        )).scalar_one_or_none()
        if conflict:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already in use")
        current_user.email = body.email

    if body.full_name is not None:
        current_user.full_name = body.full_name

    await db.commit()
    await db.refresh(current_user)
    return UserPublic.model_validate(current_user)
