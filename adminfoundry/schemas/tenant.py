import re
import uuid
from datetime import datetime
from pydantic import BaseModel, field_validator

# Slugs that may not be used as tenant identifiers.
# This is the single source of truth — middleware/tenant.py imports from here.
# Slugs in the first group are blocked by subdomain routing; creating such a
# tenant would make it unreachable via subdomain.
RESERVED_SLUGS: frozenset[str] = frozenset({
    # Blocked by subdomain routing — tenant would be unreachable
    "www", "admin", "api", "mail", "smtp", "ftp", "localhost",
    # PostgreSQL / SQL reserved names
    "public",
    # Framework / infra namespaces
    "static", "assets", "uploads", "health", "tenant", "tenants",
    "root", "system", "default", "shared", "app",
})

# No trailing hyphen; max 56 chars so that "tenant_" + slug fits in
# PostgreSQL's 63-char NAMEDATALEN limit for unquoted identifiers.
# Pattern: single alphanum  OR  alphanum + (middle chars) + alphanum
_SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,54}[a-z0-9])?$")


def _validate_slug(v: str) -> str:
    if not _SLUG_RE.match(v):
        raise ValueError(
            "slug must start and end with a lowercase letter or digit, "
            "contain only lowercase letters, digits, or hyphens, "
            "and be 1–56 characters"
        )
    if v in RESERVED_SLUGS:
        raise ValueError(f"'{v}' is a reserved slug and cannot be used")
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
