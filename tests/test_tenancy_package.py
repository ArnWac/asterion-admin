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


# ---------------------------------------------------------------------------
# TenantAuthContext — unit tests (no DB required)
# ---------------------------------------------------------------------------

def _make_auth_ctx(role_names=(), permission_keys=()):
    from unittest.mock import MagicMock
    from adminfoundry.tenancy.context import TenantAuthContext, TenantContext

    tenant = TenantContext.from_dict({
        "id": str(uuid.uuid4()), "slug": "acme", "name": "Acme",
        "is_active": True,
    })
    membership = MagicMock()
    roles = [MagicMock(name=n) for n in role_names]
    for r, n in zip(roles, role_names):
        r.name = n
    return TenantAuthContext(
        tenant=tenant,
        membership=membership,
        roles=roles,
        permission_keys=set(permission_keys),
    )


def test_tenant_auth_context_has_permission():
    ctx = _make_auth_ctx(permission_keys=["admin.users.list", "admin.users.create"])
    assert ctx.has_permission("admin.users.list")
    assert not ctx.has_permission("admin.users.delete")


def test_tenant_auth_context_has_role():
    ctx = _make_auth_ctx(role_names=["admin", "viewer"])
    assert ctx.has_role("admin")
    assert not ctx.has_role("owner")


def test_tenant_auth_context_role_names():
    ctx = _make_auth_ctx(role_names=["admin", "viewer"])
    assert ctx.role_names() == frozenset({"admin", "viewer"})


def test_tenant_auth_context_empty_by_default():
    ctx = _make_auth_ctx()
    assert ctx.role_names() == frozenset()
    assert not ctx.has_permission("anything")
    assert not ctx.has_role("admin")
