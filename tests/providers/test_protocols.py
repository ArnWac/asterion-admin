"""Unit tests for the provider protocols + builtin default implementations.

Each of the four providers gets:
* a Protocol-conformance check (built-in implements it, fake external
  implements it),
* a smoke test for the default behaviour where it can be exercised
  without spinning up a full FastAPI app.

The wider end-to-end test (a fake external provider plugged into
``create_admin``) lives in tests/providers/test_external_e2e.py so the
two concerns stay separable.
"""

from __future__ import annotations

import pytest

from asterion.providers import (
    AdminPrincipal,
    AdminTenant,
    AuthIdentity,
    AuthProvider,
    BuiltinJWTAuthProvider,
    BuiltinPermissionProvider,
    BuiltinSQLAlchemyUserProvider,
    BuiltinTenantProvider,
    PermissionProvider,
    TenantProvider,
    UserProvider,
)

# --- DTO sanity ---


def test_admin_user_is_frozen():
    u = AdminPrincipal(id="42", email="a@b.c")
    with pytest.raises(Exception):
        u.email = "x@y.z"  # frozen dataclass


def test_auth_identity_carries_claims():
    ident = AuthIdentity(user_id="42", claims={"tkv": 5, "scope": "admin"})
    assert ident.user_id == "42"
    assert ident.claims["scope"] == "admin"


def test_admin_tenant_minimal_fields():
    t = AdminTenant(id="x", slug="acme")
    assert t.slug == "acme"
    assert t.name is None


# --- Built-in providers conform to their protocols ---


def test_builtin_jwt_provider_is_auth_provider():
    assert isinstance(BuiltinJWTAuthProvider(), AuthProvider)


def test_builtin_user_provider_is_user_provider():
    assert isinstance(BuiltinSQLAlchemyUserProvider(), UserProvider)


def test_builtin_permission_provider_is_permission_provider():
    assert isinstance(BuiltinPermissionProvider(), PermissionProvider)


def test_builtin_tenant_provider_is_tenant_provider():
    assert isinstance(BuiltinTenantProvider(), TenantProvider)


# --- BuiltinPermissionProvider direct behavioural checks (no FastAPI needed) ---


@pytest.mark.asyncio
async def test_permission_provider_superadmin_gets_wildcard():
    p = BuiltinPermissionProvider()
    user = AdminPrincipal(id="42", is_superadmin=True)
    perms = await p.get_permissions(user, tenant=None)
    assert "admin.*" in perms


@pytest.mark.asyncio
async def test_permission_provider_no_tenant_no_perms():
    p = BuiltinPermissionProvider()
    user = AdminPrincipal(id="42", is_superadmin=False)
    perms = await p.get_permissions(user, tenant=None)
    assert perms == frozenset()


def test_permission_provider_is_superadmin_flag():
    p = BuiltinPermissionProvider()
    assert p.is_superadmin(AdminPrincipal(id="42", is_superadmin=True)) is True
    assert p.is_superadmin(AdminPrincipal(id="43", is_superadmin=False)) is False


# --- Fake external providers also conform to the Protocols ---


class FakeOAuthProvider:
    async def authenticate_request(self, request):
        return AuthIdentity(user_id="user-from-oauth")


class FakeRestUserProvider:
    async def get_by_id(self, user_id, *, request=None):
        return AdminPrincipal(id=user_id, email=f"{user_id}@example.com")


class FakeRBACProvider:
    def is_superadmin(self, user):
        return False

    async def get_permissions(self, user, tenant, *, request=None):
        return frozenset({"admin.posts.list", "admin.posts.read"})


class FakeJwtTenantProvider:
    async def resolve_tenant(self, request):
        return AdminTenant(id="t-1", slug="acme", name="Acme")


def test_fake_oauth_is_auth_provider():
    assert isinstance(FakeOAuthProvider(), AuthProvider)


def test_fake_user_provider_is_user_provider():
    assert isinstance(FakeRestUserProvider(), UserProvider)


def test_fake_rbac_is_permission_provider():
    assert isinstance(FakeRBACProvider(), PermissionProvider)


def test_fake_jwt_tenant_is_tenant_provider():
    assert isinstance(FakeJwtTenantProvider(), TenantProvider)
