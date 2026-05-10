import math
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from adminfoundry.database import get_db
from adminfoundry.pagination import paginate
from adminfoundry.dependencies import require_superadmin
from adminfoundry.models.user import User
from adminfoundry.models.role import Role, user_roles
from adminfoundry.schemas.common import PaginatedResponse
from adminfoundry.schemas.role import RolePublic, RoleCreate

router = APIRouter(tags=["roles"])


@router.get("/api/v1/roles", response_model=PaginatedResponse[RolePublic])
async def list_roles(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    items, total, pages = await paginate(db, select(Role), page, page_size)
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size, pages=pages)


@router.post("/api/v1/roles", response_model=RolePublic, status_code=status.HTTP_201_CREATED)
async def create_role(
    body: RoleCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    existing = (await db.execute(select(Role).where(Role.name == body.name))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Role name already exists")

    role = Role(name=body.name)
    db.add(role)
    await db.commit()
    await db.refresh(role)
    return role


@router.get("/api/v1/roles/{role_id}", response_model=RolePublic)
async def get_role(
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    return role


@router.delete("/api/v1/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    await db.delete(role)
    await db.commit()


# --- Role assignment ---

@router.post(
    "/api/v1/users/{user_id}/roles/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["users"],
)
async def assign_role(
    user_id: uuid.UUID,
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    # Reject duplicate assignment cleanly
    already = await db.execute(
        select(user_roles).where(
            user_roles.c.user_id == user_id,
            user_roles.c.role_id == role_id,
        )
    )
    if already.first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Role already assigned")

    await db.execute(user_roles.insert().values(user_id=user_id, role_id=role_id))
    await db.commit()


@router.delete(
    "/api/v1/users/{user_id}/roles/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["users"],
)
async def remove_role(
    user_id: uuid.UUID,
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    result = await db.execute(
        delete(user_roles).where(
            user_roles.c.user_id == user_id,
            user_roles.c.role_id == role_id,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")
    await db.commit()
