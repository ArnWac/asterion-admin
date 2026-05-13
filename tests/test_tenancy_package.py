"""
Tests for the adminfoundry.tenancy package.

- TenantContext creation from ORM object and from dict.
- SchemaTenantStrategy rejects unsafe schema names.
"""
import uuid
import pytest


# ---------------------------------------------------------------------------
# TenantContext
# ---------------------------------------------------------------------------

def test_tenant_context_from_dict():
    from adminfoundry.tenancy.context import TenantContext

    data = {
        "id": str(uuid.uuid4()),
        "slug": "acme",
        "name": "Acme Corp",
        "is_active": True,
        "timezone": "Europe/Berlin",
        "language": "de",
        "date_format": "eu",
        "date_pattern": None,
        "allowed_cidrs": None,
    }
    ctx = TenantContext.from_dict(data)

    assert ctx.slug == "acme"
    assert ctx.name == "Acme Corp"
    assert ctx.is_active is True
    assert ctx.schema_name == "tenant_acme"
    assert ctx.timezone == "Europe/Berlin"
    assert ctx.language == "de"
    assert ctx.is_superadmin_context is False


def test_tenant_context_to_dict_roundtrip():
    from adminfoundry.tenancy.context import TenantContext

    original = {
        "id": str(uuid.uuid4()),
        "slug": "beta",
        "name": "Beta Ltd",
        "is_active": True,
        "timezone": None,
        "language": "en",
        "date_format": None,
        "date_pattern": None,
        "allowed_cidrs": None,
    }
    ctx = TenantContext.from_dict(original)
    roundtripped = ctx.to_dict()

    assert roundtripped["slug"] == "beta"
    assert roundtripped["id"] == original["id"]


def test_tenant_context_schema_name_derived_from_slug():
    from adminfoundry.tenancy.context import TenantContext

    ctx = TenantContext.from_dict({
        "id": str(uuid.uuid4()),
        "slug": "my-company",
        "name": "My Company",
        "is_active": True,
    })
    assert ctx.schema_name == "tenant_my-company"


# ---------------------------------------------------------------------------
# SchemaTenantStrategy — schema name injection guard
# ---------------------------------------------------------------------------

def test_schema_strategy_rejects_unsafe_name():
    from adminfoundry.tenancy.schema_strategy import _validate_schema_name

    with pytest.raises(ValueError, match="Unsafe schema name"):
        _validate_schema_name("tenant_; DROP TABLE users--")


def test_schema_strategy_rejects_missing_prefix():
    from adminfoundry.tenancy.schema_strategy import _validate_schema_name

    with pytest.raises(ValueError, match="Unsafe schema name"):
        _validate_schema_name("public")


def test_schema_strategy_rejects_uppercase():
    from adminfoundry.tenancy.schema_strategy import _validate_schema_name

    with pytest.raises(ValueError, match="Unsafe schema name"):
        _validate_schema_name("tenant_Acme")


def test_schema_strategy_accepts_valid_names():
    from adminfoundry.tenancy.schema_strategy import _validate_schema_name

    _validate_schema_name("tenant_acme")
    _validate_schema_name("tenant_acme_corp")
    _validate_schema_name("tenant_123")
    _validate_schema_name("tenant_my_company_2")
    _validate_schema_name("tenant_scope-co")   # hyphens allowed (slugs use them)
