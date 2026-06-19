"""Tests for permission key building and wildcard matching."""

from __future__ import annotations

import pytest

from asterion.authz.permissions import assert_permission, has_permission, permission_key

# --- permission_key() ---


def test_permission_key_basic():
    assert permission_key("users", "list") == "admin.users.list"


def test_permission_key_custom_namespace():
    assert permission_key("users", "list", namespace="tenant") == "tenant.users.list"


def test_permission_key_rejects_wildcards():
    with pytest.raises(ValueError):
        permission_key("users", "*")


def test_permission_key_rejects_dots():
    with pytest.raises(ValueError):
        permission_key("my.resource", "list")


# --- has_permission() ---


def test_exact_match():
    assert has_permission(["admin.users.list"], "admin.users.list") is True


def test_no_match():
    assert has_permission(["admin.users.list"], "admin.users.delete") is False


def test_wildcard_star_matches_any_action():
    assert has_permission(["admin.users.*"], "admin.users.list") is True
    assert has_permission(["admin.users.*"], "admin.users.delete") is True


def test_global_wildcard_admin_star_grants_any_admin_action():
    assert has_permission(["admin.*"], "admin.users.list") is True
    assert has_permission(["admin.*"], "admin.projects.delete") is True


def test_wildcard_does_not_cross_namespace():
    assert has_permission(["tenant.*"], "admin.users.list") is False


def test_middle_wildcard_rejected_as_required():
    with pytest.raises(ValueError):
        has_permission(["admin.*"], "admin.*.list")


def test_empty_granted_returns_false():
    assert has_permission([], "admin.users.list") is False


def test_multiple_grants_any_match():
    assert (
        has_permission(["admin.users.list", "admin.projects.create"], "admin.projects.create")
        is True
    )


# --- assert_permission() ---


def test_assert_permission_raises_on_failure():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        assert_permission([], "admin.users.list")
    assert exc_info.value.status_code == 403
