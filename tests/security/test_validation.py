"""Tests for input validators in asterion.security.validation."""

from __future__ import annotations

import pytest

from asterion.security.validation import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    InvalidActionNameError,
    InvalidPermissionKeyError,
    InvalidResourceNameError,
    InvalidSchemaNameError,
    InvalidTenantSlugError,
    ValidationError,
    validate_action_name,
    validate_limit_offset,
    validate_permission_key,
    validate_resource_name,
    validate_schema_name,
    validate_tenant_slug,
)

# --- resource ---


@pytest.mark.parametrize("value", ["users", "user-profiles", "u", "abc_123"])
def test_resource_accepts_valid(value):
    assert validate_resource_name(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "1users",
        "Users",
        "users!",
        "users.profile",
        "users/admin",
        "a" * 64,
    ],
)
def test_resource_rejects_invalid(value):
    with pytest.raises(InvalidResourceNameError):
        validate_resource_name(value)


def test_resource_rejects_non_string():
    with pytest.raises(ValidationError):
        validate_resource_name(123)  # type: ignore[arg-type]


# --- action ---


@pytest.mark.parametrize("value", ["list", "soft_delete", "x"])
def test_action_accepts_valid(value):
    assert validate_action_name(value) == value


@pytest.mark.parametrize("value", ["", "List", "soft-delete", "1delete", "a" * 64])
def test_action_rejects_invalid(value):
    with pytest.raises(InvalidActionNameError):
        validate_action_name(value)


# --- tenant slug ---


@pytest.mark.parametrize("value", ["acme", "ac", "my-company", "tenant-001"])
def test_tenant_slug_accepts_valid(value):
    assert validate_tenant_slug(value) == value


@pytest.mark.parametrize(
    "raw,expected",
    [("Acme", "acme"), ("  acme  ", "acme"), ("Foo-Bar", "foo-bar")],
)
def test_tenant_slug_normalizes_case_and_whitespace(raw, expected):
    # Review R12: casing / surrounding whitespace are normalized rather than
    # rejected, so a client header like "Acme" resolves the stored "acme".
    assert validate_tenant_slug(raw) == expected


@pytest.mark.parametrize(
    "value",
    ["", "a", "_acme", "ac_me", "ac.me", "1acme", "has space", "a" * 64],
)
def test_tenant_slug_rejects_invalid(value):
    with pytest.raises(InvalidTenantSlugError):
        validate_tenant_slug(value)


# --- schema ---


@pytest.mark.parametrize("value", ["tenant_acme", "tenant_001", "tenant_ac_me"])
def test_schema_accepts_valid(value):
    assert validate_schema_name(value) == value


def test_schema_rejects_reserved():
    for name in ["public", "information_schema", "pg_catalog", "pg_toast"]:
        with pytest.raises(InvalidSchemaNameError):
            validate_schema_name(name)


def test_schema_rejects_pg_prefix():
    with pytest.raises(InvalidSchemaNameError):
        validate_schema_name("pg_custom")


@pytest.mark.parametrize(
    "value",
    ["", "tenant-acme", "Tenant_acme", "tenant acme", "1tenant", "a" * 64],
)
def test_schema_rejects_invalid(value):
    with pytest.raises(InvalidSchemaNameError):
        validate_schema_name(value)


# --- permission key ---


@pytest.mark.parametrize(
    "value",
    [
        "admin.users.list",
        "admin.user-profiles.read",
        "admin.users.*",
        "admin.*",
        "tenant.users.list",
    ],
)
def test_permission_key_accepts_valid(value):
    assert validate_permission_key(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "admin",
        "admin.users",
        "admin.users.list.extra",
        "admin..list",
        "Admin.users.list",
        "admin.*.list",
        "admin.*.*",
        "*.users.list",
        "admin.users.LIST",
        "admin.users.list ",
    ],
)
def test_permission_key_rejects_invalid(value):
    with pytest.raises(InvalidPermissionKeyError):
        validate_permission_key(value)


def test_permission_key_middle_wildcard_rejected():
    with pytest.raises(InvalidPermissionKeyError):
        validate_permission_key("admin.*.list")


# --- limit/offset ---


def test_limit_offset_defaults():
    limit, offset = validate_limit_offset()
    assert limit == DEFAULT_PAGE_LIMIT
    assert offset == 0


def test_limit_capped_at_max():
    limit, _ = validate_limit_offset(limit=10_000)
    assert limit == MAX_PAGE_LIMIT


def test_limit_floor_at_one():
    limit, _ = validate_limit_offset(limit=0)
    assert limit == 1
    limit, _ = validate_limit_offset(limit=-50)
    assert limit == 1


def test_negative_offset_clamps_to_zero():
    _, offset = validate_limit_offset(offset=-1)
    assert offset == 0


def test_non_int_limit_rejected():
    with pytest.raises(ValidationError):
        validate_limit_offset(limit="20")  # type: ignore[arg-type]


def test_non_int_offset_rejected():
    with pytest.raises(ValidationError):
        validate_limit_offset(offset="0")  # type: ignore[arg-type]


def test_bool_not_accepted_as_int():
    with pytest.raises(ValidationError):
        validate_limit_offset(limit=True)  # type: ignore[arg-type]
