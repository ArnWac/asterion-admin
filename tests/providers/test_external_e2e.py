"""End-to-end proof that ``create_admin`` honours external providers.

The framework's own routers don't yet consume :class:`AdminContext`
(Phase 2 of the v1-providers refactor), so this test mounts a tiny ad-hoc
route that depends on :func:`build_admin_context` and asserts that the
context is assembled from the fake external providers we passed in —
not from the built-in JWT/SQLAlchemy stack.

This is the contract test for the SPI: if these assertions break, the
provider plumbing has regressed and external integrations stop working.
"""

from __future__ import annotations

import asyncio

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from asterion import CoreAdminConfig, create_admin
from asterion.admin import AdminContext, build_admin_context
from asterion.providers.base import AdminPrincipal, AdminTenant, AuthIdentity

# --- Fakes ---


class _StaticAuthProvider:
    """Always returns the same identity, ignores credentials. Test-only."""

    def __init__(self, user_id: str = "ext-1", claims: dict | None = None):
        self.user_id = user_id
        self.claims = claims or {"iss": "google-fake"}
        self.called: int = 0

    async def authenticate_request(self, request):
        self.called += 1
        # Pretend a header presence is the credential.
        if "x-fake-auth" not in request.headers:
            return None
        return AuthIdentity(user_id=self.user_id, claims=dict(self.claims))


class _StaticUserProvider:
    """Hands back a hard-coded :class:`AdminPrincipal` for the known id."""

    def __init__(self, user: AdminPrincipal):
        self.user = user

    async def get_by_id(self, user_id, *, request=None):
        if user_id == self.user.id:
            return self.user
        return None


class _StaticPermissionProvider:
    """Grants a fixed permission set; ignores tenant."""

    def __init__(self, perms: frozenset[str]):
        self.perms = perms

    def is_superadmin(self, user):
        return user.is_superadmin

    async def get_permissions(self, user, tenant, *, request=None):
        if user.is_superadmin:
            return frozenset({"admin.*"})
        return self.perms


class _StaticTenantProvider:
    def __init__(self, tenant: AdminTenant | None):
        self.tenant = tenant

    async def resolve_tenant(self, request):
        return self.tenant


# --- Helpers ---


def _build_app(
    tmp_path,
    *,
    auth_provider,
    user_provider,
    permission_provider,
    tenant_provider,
) -> FastAPI:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'ext.db'}"
    app = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key="test-ext-providers",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        ),
        auth_provider=auth_provider,
        user_provider=user_provider,
        permission_provider=permission_provider,
        tenant_provider=tenant_provider,
    )

    # Add a probe route that returns whatever AdminContext was built.
    @app.get("/_probe")
    async def probe(ctx: AdminContext = Depends(build_admin_context)):
        return {
            "principal_id": ctx.principal.id if ctx.principal else None,
            "principal_email": ctx.principal.email if ctx.principal else None,
            "tenant_slug": ctx.tenant.slug if ctx.tenant else None,
            "permissions": sorted(ctx.permissions),
            "is_authenticated": ctx.is_authenticated,
            "is_superadmin": ctx.is_superadmin,
        }

    return app


# --- Tests ---


def test_external_providers_round_trip(tmp_path):
    """The single canonical proof that all four providers flow through to
    AdminContext.

    A real Google OAuth integration would replace these four fakes with
    real adapters; nothing else in the framework needs to change.
    """
    user = AdminPrincipal(id="ext-1", email="alice@google.example", display_name="Alice")
    auth = _StaticAuthProvider(user_id=user.id, claims={"iss": "google"})
    users = _StaticUserProvider(user)
    perms = _StaticPermissionProvider(frozenset({"admin.posts.list", "admin.posts.read"}))
    tenants = _StaticTenantProvider(AdminTenant(id="t1", slug="acme", name="Acme"))

    app = _build_app(
        tmp_path,
        auth_provider=auth,
        user_provider=users,
        permission_provider=perms,
        tenant_provider=tenants,
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        # Without the fake credential header, the auth provider returns None.
        anon = client.get("/_probe").json()
        assert anon == {
            "principal_id": None,
            "principal_email": None,
            "tenant_slug": "acme",
            "permissions": [],
            "is_authenticated": False,
            "is_superadmin": False,
        }

        # With the credential header, the full context is assembled.
        authed = client.get("/_probe", headers={"x-fake-auth": "yes"}).json()
        assert authed == {
            "principal_id": "ext-1",
            "principal_email": "alice@google.example",
            "tenant_slug": "acme",
            "permissions": ["admin.posts.list", "admin.posts.read"],
            "is_authenticated": True,
            "is_superadmin": False,
        }

    assert auth.called >= 2


def test_external_superadmin_grants_wildcard(tmp_path):
    user = AdminPrincipal(id="su-1", email="root@google.example", is_superadmin=True)
    auth = _StaticAuthProvider(user_id=user.id)
    users = _StaticUserProvider(user)
    perms = _StaticPermissionProvider(frozenset())  # no app-defined perms
    tenants = _StaticTenantProvider(None)

    app = _build_app(
        tmp_path,
        auth_provider=auth,
        user_provider=users,
        permission_provider=perms,
        tenant_provider=tenants,
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        body = client.get("/_probe", headers={"x-fake-auth": "yes"}).json()

    assert body["is_superadmin"] is True
    # Superadmin path returns the wildcard via the provider's own logic.
    assert "admin.*" in body["permissions"]


def test_default_providers_remain_active_when_not_overridden(tmp_path):
    """Sanity check on the default path — building create_admin without any
    provider kwargs must produce the four Builtin* providers."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'default.db'}"
    app = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key="test-defaults",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        )
    )
    providers = app.state.asterion.providers
    assert type(providers.auth).__name__ == "BuiltinJWTAuthProvider"
    assert type(providers.users).__name__ == "BuiltinSQLAlchemyUserProvider"
    assert type(providers.permissions).__name__ == "BuiltinPermissionProvider"
    assert type(providers.tenants).__name__ == "BuiltinTenantProvider"

    # cleanup
    asyncio.run(app.state.asterion.db.dispose())
