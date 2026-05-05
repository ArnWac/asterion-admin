import uuid
from datetime import datetime
from pydantic import BaseModel, EmailStr
from adminfoundry.schemas.role import RolePublic


class UserPublic(BaseModel):
    id: uuid.UUID
    email: EmailStr
    full_name: str | None
    is_active: bool
    is_superadmin: bool
    created_at: datetime
    updated_at: datetime
    roles: list[RolePublic] = []

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: str | None = None
    is_superadmin: bool = False


class UserUpdate(BaseModel):
    full_name: str | None = None
    is_active: bool | None = None
    is_superadmin: bool | None = None
