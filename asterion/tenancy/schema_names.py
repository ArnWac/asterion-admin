from __future__ import annotations

import re

from asterion.security.validation import (
    InvalidSchemaNameError,
    validate_schema_name,
    validate_tenant_slug,
)

__all__ = [
    "InvalidSchemaNameError",
    "make_tenant_schema_name",
    "validate_schema_name",
]


def make_tenant_schema_name(slug: str) -> str:
    """Produce a safe tenant schema name from a slug.

    The slug must already be a valid tenant slug. Hyphens are translated to
    underscores so the result is a valid SQL identifier.
    """
    slug = validate_tenant_slug(slug)
    normalized = slug.replace("-", "_")
    normalized = re.sub(r"_+", "_", normalized).strip("_")

    if not normalized:
        raise InvalidSchemaNameError("Tenant slug does not produce a valid schema name.")

    schema_name = f"tenant_{normalized}"[:63].rstrip("_")
    return validate_schema_name(schema_name)
