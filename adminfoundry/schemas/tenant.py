import re
import uuid
from datetime import datetime
from pydantic import BaseModel, field_validator

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def _validate_slug(v: str) -> str:
    if not _SLUG_RE.match(v):
        raise ValueError(
            "slug must start with a lowercase letter or digit, "
            "contain only lowercase letters, digits, or hyphens, "
            "and be 1–63 characters"
        )
    return v


class TenantPublic(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    is_active: bool
    schema_name: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TenantCreate(BaseModel):
    name: str
    slug: str

    @field_validator("slug")
    @classmethod
    def slug_valid(cls, v: str) -> str:
        return _validate_slug(v)


class TenantUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None
