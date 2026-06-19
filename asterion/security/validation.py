"""Input validation primitives for resource, action, tenant, schema, permission keys.

These validators are the single source of truth for what is considered a safe
identifier inside asterion. They are invoked at every system boundary
(registry registration, CRUD lookup, tenant bootstrap, permission-key construction,
pagination) to keep untrusted input out of the rest of the codebase.
"""

from __future__ import annotations

import re

MAX_PAGE_LIMIT = 500
DEFAULT_PAGE_LIMIT = 100

_RESOURCE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
_ACTION_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_TENANT_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
_SCHEMA_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_NAMESPACE_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_PERMISSION_SEGMENT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")

_RESERVED_SCHEMA_NAMES = frozenset({"public", "information_schema", "pg_catalog", "pg_toast"})


class ValidationError(ValueError):
    """Base error for security input validation."""


class InvalidResourceNameError(ValidationError):
    pass


class InvalidActionNameError(ValidationError):
    pass


class InvalidTenantSlugError(ValidationError):
    pass


class InvalidSchemaNameError(ValidationError):
    pass


class InvalidPermissionKeyError(ValidationError):
    pass


def _require_str(value: object, *, name: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be a string, got {type(value).__name__}")
    return value


def validate_resource_name(value: str) -> str:
    """Resource: lowercase, starts with a letter, ``[a-z0-9_-]`` allowed."""
    value = _require_str(value, name="resource name")
    if not _RESOURCE_RE.fullmatch(value):
        raise InvalidResourceNameError(
            f"Invalid resource name: {value!r}. "
            "Must start with a lowercase letter and contain only "
            "lowercase letters, digits, hyphens or underscores (max length 63)."
        )
    return value


def validate_action_name(value: str) -> str:
    """Action: lowercase, starts with a letter, ``[a-z0-9_]`` only (no hyphens)."""
    value = _require_str(value, name="action name")
    if not _ACTION_RE.fullmatch(value):
        raise InvalidActionNameError(
            f"Invalid action name: {value!r}. "
            "Must start with a lowercase letter and contain only "
            "lowercase letters, digits or underscores (max length 63)."
        )
    return value


def validate_tenant_slug(value: str) -> str:
    """Tenant slug: at least 2 chars, lowercase, hyphen-friendly.

    Normalizes (strip + lowercase) before validating (Review R12) so callers
    can pass ``"  Acme "`` and get the canonical ``"acme"``; genuinely invalid
    shapes (spaces, punctuation, too short) still raise.
    """
    value = _require_str(value, name="tenant slug").strip().lower()
    if not _TENANT_SLUG_RE.fullmatch(value):
        raise InvalidTenantSlugError(
            f"Invalid tenant slug: {value!r}. "
            "Must start with a lowercase letter and contain only "
            "lowercase letters, digits or hyphens (length 2-63)."
        )
    return value


def validate_schema_name(value: str) -> str:
    """PostgreSQL schema name: safe identifier, not reserved."""
    value = _require_str(value, name="schema name")
    if value in _RESERVED_SCHEMA_NAMES:
        raise InvalidSchemaNameError(f"Reserved schema name: {value!r}")
    if value.startswith("pg_"):
        raise InvalidSchemaNameError("Schema names starting with 'pg_' are reserved.")
    if not _SCHEMA_NAME_RE.fullmatch(value):
        raise InvalidSchemaNameError(
            f"Invalid schema name: {value!r}. "
            "Must start with a lowercase letter and contain only "
            "lowercase letters, digits or underscores (max length 63)."
        )
    return value


def validate_permission_key(value: str) -> str:
    """Permission key: ``namespace.resource.action`` plus trailing wildcards.

    Accepted forms::

        admin.project.read
        admin.project.*
        admin.*

    Rejected::

        admin.*.read         # middle wildcard
        admin..read          # empty segment
        Admin.Project.read   # mixed case
        admin.project        # too few segments
    """
    value = _require_str(value, name="permission key")
    if not value:
        raise InvalidPermissionKeyError("Permission key must not be empty.")
    segments = value.split(".")

    if len(segments) not in (2, 3):
        raise InvalidPermissionKeyError(
            f"Invalid permission key: {value!r}. "
            "Expected '<namespace>.<resource>.<action>' or trailing-wildcard form."
        )

    if not _NAMESPACE_RE.fullmatch(segments[0]):
        raise InvalidPermissionKeyError(
            f"Invalid permission key namespace: {segments[0]!r} in {value!r}"
        )

    if len(segments) == 2:
        if segments[1] != "*":
            raise InvalidPermissionKeyError(
                f"Two-segment permission keys must end with a wildcard: {value!r}"
            )
        return value

    resource, action = segments[1], segments[2]
    if resource == "*":
        raise InvalidPermissionKeyError(f"Middle wildcard is not allowed: {value!r}")
    if not _PERMISSION_SEGMENT_RE.fullmatch(resource):
        raise InvalidPermissionKeyError(
            f"Invalid permission key resource: {resource!r} in {value!r}"
        )
    if action != "*" and not _PERMISSION_SEGMENT_RE.fullmatch(action):
        raise InvalidPermissionKeyError(f"Invalid permission key action: {action!r} in {value!r}")

    return value


def validate_limit_offset(
    *,
    limit: int | None = None,
    offset: int | None = None,
    max_limit: int = MAX_PAGE_LIMIT,
    default_limit: int = DEFAULT_PAGE_LIMIT,
) -> tuple[int, int]:
    """Coerce + bound a pagination pair. Negative offsets clamp to 0."""
    if limit is None:
        normalized_limit = default_limit
    else:
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise ValidationError(f"limit must be int, got {type(limit).__name__}")
        normalized_limit = max(1, min(limit, max_limit))

    if offset is None:
        normalized_offset = 0
    else:
        if not isinstance(offset, int) or isinstance(offset, bool):
            raise ValidationError(f"offset must be int, got {type(offset).__name__}")
        normalized_offset = max(0, offset)

    return normalized_limit, normalized_offset
