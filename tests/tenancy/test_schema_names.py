"""Tests for tenant schema name validation."""

from __future__ import annotations

import pytest

from asterion.tenancy.schema_strategy import _validate_schema_name


def test_valid_schema_name():
    _validate_schema_name("tenant_mycompany")


def test_valid_schema_with_numbers():
    _validate_schema_name("tenant_company123")


def test_hyphens_rejected_in_schema_name():
    """Schema identifiers must not contain hyphens (slug hyphens are
    translated to underscores by make_tenant_schema_name)."""
    with pytest.raises(ValueError):
        _validate_schema_name("tenant_my-company")


def test_invalid_schema_no_prefix():
    with pytest.raises(ValueError):
        _validate_schema_name("mycompany")


def test_invalid_schema_uppercase():
    with pytest.raises(ValueError):
        _validate_schema_name("tenant_MyCompany")


def test_invalid_schema_injection_attempt():
    with pytest.raises(ValueError):
        _validate_schema_name("tenant_hack; DROP TABLE users;")


def test_invalid_empty():
    with pytest.raises(ValueError):
        _validate_schema_name("")
