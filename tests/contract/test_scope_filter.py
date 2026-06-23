"""Phase A — context-aware sidebar filtering.

The full-contract endpoint (`/_contract`) feeds the admin sidebar and the
dashboard. In multi-tenant mode it must list only the resources reachable in
the current request scope:

* outside a tenant (public schema, e.g. a superadmin not inside any tenant)
  only **global** models resolve — tenant-scoped tables don't exist in
  ``public``, so a link to one would 500 with "relation does not exist";
* inside a tenant only **tenant-scoped** models resolve.

Single-tenant apps (``enable_multi_tenant=False``, no TenantMiddleware, so
``ctx.tenant`` is always None) skip the filter entirely and see everything.

``resolve_model_scope`` derives the scope from the SQLAlchemy base —
``TenantModel`` → ``"tenant"``, everything else → ``"global"``.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import Column, String

from asterion import CoreAdminConfig, ModelAdmin, create_admin
from asterion.contract.service import build_model_contract, resolve_model_scope
from asterion.models.base import GlobalModel, TenantModel
from tests._helpers import make_admin_principal, make_admin_tenant, override_admin_context

ADMIN = "/api/v1/admin"


class ScopeGlobalThing(GlobalModel):
    __tablename__ = "scopefilter_globals"
    name = Column(String(100), nullable=False)


class ScopeTenantThing(TenantModel):
    __tablename__ = "scopefilter_tenants"
    name = Column(String(100), nullable=False)


class GlobalThingAdmin(ModelAdmin):
    model = ScopeGlobalThing
    list_display = ["id", "name"]


class TenantThingAdmin(ModelAdmin):
    model = ScopeTenantThing
    list_display = ["id", "name"]


# ---------------------------------------------------------------------------
# Unit: scope derivation
# ---------------------------------------------------------------------------


def test_resolve_model_scope_tenant_model():
    assert resolve_model_scope(TenantThingAdmin()) == "tenant"


def test_resolve_model_scope_global_model():
    assert resolve_model_scope(GlobalThingAdmin()) == "global"


def test_scope_in_built_contract():
    assert build_model_contract(GlobalThingAdmin()).scope == "global"
    assert build_model_contract(TenantThingAdmin()).scope == "tenant"


def test_show_in_nav_default_true_and_builtin_flag():
    from asterion.builtins.admin import TenantRolePermissionAdmin

    # Default is True; the flat tenant-role-permission admin opts out so the
    # sidebar shows only Tenant Roles (permissions edited via the picker).
    assert build_model_contract(GlobalThingAdmin()).show_in_nav is True
    assert build_model_contract(TenantRolePermissionAdmin()).show_in_nav is False


def test_scope_defaults_to_global_for_unknown_base():
    """A model on neither GlobalBase nor TenantBase stays visible in the
    public view (safe default) rather than vanishing."""
    from sqlalchemy import Integer
    from sqlalchemy.orm import DeclarativeBase

    class _Base(DeclarativeBase):
        pass

    class _Plain(_Base):
        __tablename__ = "scopefilter_plain"
        id = Column(Integer, primary_key=True)

    class _PlainAdmin(ModelAdmin):
        model = _Plain

    assert resolve_model_scope(_PlainAdmin()) == "global"


# ---------------------------------------------------------------------------
# Endpoint: multi-tenant filtering
# ---------------------------------------------------------------------------


def _make_app(*, multi_tenant: bool, tmp_path, name: str):
    application = create_admin(
        config=CoreAdminConfig(
            database_url=f"sqlite+aiosqlite:///{tmp_path / name}",
            secret_key="test-scope-secret",
            enable_multi_tenant=multi_tenant,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        ),
        register=lambda reg: (
            reg.register(GlobalThingAdmin),
            reg.register(TenantThingAdmin),
        ),
    )
    return application


def _resources(client: TestClient) -> set[str]:
    resp = client.get(f"{ADMIN}/_contract")
    assert resp.status_code == 200, resp.text
    return {m["resource"] for m in resp.json()["models"]}


def test_public_context_hides_tenant_models(tmp_path):
    app = _make_app(multi_tenant=True, tmp_path=tmp_path, name="mt_public.db")
    override_admin_context(app, principal=make_admin_principal(is_superadmin=True), tenant=None)
    resources = _resources(TestClient(app))
    assert "scopefilter_globals" in resources
    assert "scopefilter_tenants" not in resources


def test_tenant_context_hides_global_models(tmp_path):
    app = _make_app(multi_tenant=True, tmp_path=tmp_path, name="mt_tenant.db")
    override_admin_context(
        app,
        principal=make_admin_principal(is_superadmin=True),
        tenant=make_admin_tenant("acme"),
        permissions=frozenset({"admin.*"}),
    )
    resources = _resources(TestClient(app))
    assert "scopefilter_tenants" in resources
    assert "scopefilter_globals" not in resources


def test_single_tenant_shows_everything(tmp_path):
    """No multi-tenancy → no filter; both scopes are visible."""
    app = _make_app(multi_tenant=False, tmp_path=tmp_path, name="st.db")
    override_admin_context(app, principal=make_admin_principal(is_superadmin=True), tenant=None)
    resources = _resources(TestClient(app))
    assert {"scopefilter_globals", "scopefilter_tenants"} <= resources
