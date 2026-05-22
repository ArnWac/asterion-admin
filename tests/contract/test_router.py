"""HTTP integration tests for the admin contract router.

Exercises the real /_contract and /_contract/{resource} endpoints against
a built FastAPI app with a registered ModelAdmin. The auth dependency is
overridden so the tests focus on contract shape and not on auth flow
(auth flow lives in tests/security/test_auth_invariants.py).
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, String
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from adminfoundry import CoreAdminConfig, ModelAdmin, create_admin
from adminfoundry.auth.password import hash_password
from adminfoundry.contract.service import CONTRACT_VERSION
from adminfoundry.models.base import GlobalModel
from adminfoundry.models.user import User
from tests._helpers import make_admin_principal, override_admin_context


class _AppBase(DeclarativeBase):
    pass


class Project(_AppBase):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    summary = Column(String(500), nullable=True)
    hashed_password = Column(String(255), nullable=True)
    internal_token = Column(String(255), nullable=True)


class ProjectAdmin(ModelAdmin):
    model = Project
    label = "Project"
    label_plural = "Projects"
    description = "User-facing projects."
    list_display = ["id", "name"]
    search_fields = ["name", "summary"]
    ordering = ["name"]
    readonly_fields = ["id"]
    protected_fields = ["internal_token"]
    calculated_fields = {"display_name": lambda obj: f"P:{obj.name}"}


@pytest.fixture
def app(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'contract.db'}"
    app = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key="test-contract-secret",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
        ),
        register=lambda reg: reg.register(ProjectAdmin),
    )

    runtime = app.state.adminfoundry

    async def _setup():
        async with runtime.db.engine.begin() as conn:
            await conn.run_sync(GlobalModel.metadata.create_all)
        factory = async_sessionmaker(runtime.db.engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                session.add(
                    User(
                        email="user@example.com",
                        hashed_password=hash_password("hunter2-strong"),
                        is_active=True,
                        is_superadmin=False,
                    )
                )

    asyncio.run(_setup())

    override_admin_context(app, principal=make_admin_principal(email="user@example.com"))

    yield app

    asyncio.run(runtime.db.dispose())


@pytest.fixture
def client(app):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# --- /_contract (full) ---


def test_full_contract_returns_version(client):
    resp = client.get("/api/v1/admin/_contract")
    assert resp.status_code == 200
    body = resp.json()
    assert body["contract_version"] == CONTRACT_VERSION
    assert isinstance(body["models"], list)


def test_full_contract_includes_registered_model(client):
    resp = client.get("/api/v1/admin/_contract")
    body = resp.json()
    resources = [m["resource"] for m in body["models"]]
    assert "projects" in resources


def test_full_contract_includes_builtin_tenant_admins(client):
    resp = client.get("/api/v1/admin/_contract")
    resources = {m["resource"] for m in resp.json()["models"]}
    assert "tenant_roles" in resources
    assert "tenant_role_permissions" in resources
    assert "tenant_membership_roles" in resources


def test_full_contract_excludes_global_user_model(client):
    """User is a global/root model — must not be registered as a
    tenant-local admin by default."""
    resp = client.get("/api/v1/admin/_contract")
    resources = {m["resource"] for m in resp.json()["models"]}
    assert "users" not in resources
    assert "tenants" not in resources
    assert "tenant_memberships" not in resources


def test_full_contract_requires_authentication(app):
    app.dependency_overrides.clear()
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/api/v1/admin/_contract")
    assert resp.status_code == 401


def test_full_contract_includes_empty_extensions_dict_by_default(client):
    """No extension contributions registered → ``extensions`` is an empty
    dict, not absent. Clients can iterate without a key check."""
    body = client.get("/api/v1/admin/_contract").json()
    assert body["extensions"] == {}


def test_full_contract_exposes_extension_contributions(tmp_path):
    """End-to-end proof of Phase 6b: an AdminExtension that adds a
    namespaced fragment via ``register_contract_contributions`` appears
    under the contract's ``extensions`` top-level key."""
    from adminfoundry import CoreAdminConfig, create_admin
    from adminfoundry.extensions import AdminExtension
    from tests._helpers import make_admin_principal, override_admin_context

    class _OAuthFake(AdminExtension):
        name = "auth_oauth"

        def register_contract_contributions(self, registry):
            registry.add(
                "auth_oauth",
                {
                    "providers": [
                        {
                            "id": "google",
                            "label": "Google",
                            "login_url": "/api/v1/oauth/google/login",
                        }
                    ]
                },
            )

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'contrib.db'}"
    app = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key="test-contrib",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        ),
        extensions=[_OAuthFake()],
    )
    override_admin_context(app, principal=make_admin_principal())

    with TestClient(app, raise_server_exceptions=False) as c:
        body = c.get("/api/v1/admin/_contract").json()

    assert "auth_oauth" in body["extensions"]
    fragment = body["extensions"]["auth_oauth"]
    assert fragment["providers"][0]["id"] == "google"
    assert fragment["providers"][0]["login_url"] == "/api/v1/oauth/google/login"


def test_full_contract_extensions_are_namespaced_per_extension(tmp_path):
    """Two extensions, two namespaces — both fragments appear, neither
    overwrites the other."""
    from adminfoundry import CoreAdminConfig, create_admin
    from adminfoundry.extensions import AdminExtension
    from tests._helpers import make_admin_principal, override_admin_context

    class _One(AdminExtension):
        name = "one"

        def register_contract_contributions(self, registry):
            registry.add("one", {"hello": "from one"})

    class _Two(AdminExtension):
        name = "two"

        def register_contract_contributions(self, registry):
            registry.add("two", {"hello": "from two"})

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'ns.db'}"
    app = create_admin(
        config=CoreAdminConfig(
            database_url=db_url,
            secret_key="test-ns",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        ),
        extensions=[_One(), _Two()],
    )
    override_admin_context(app, principal=make_admin_principal())

    with TestClient(app, raise_server_exceptions=False) as c:
        body = c.get("/api/v1/admin/_contract").json()

    assert body["extensions"] == {
        "one": {"hello": "from one"},
        "two": {"hello": "from two"},
    }


# --- /_contract/{resource} ---


def test_resource_contract_shape(client):
    resp = client.get("/api/v1/admin/_contract/projects")
    assert resp.status_code == 200
    body = resp.json()
    assert body["resource"] == "projects"
    assert body["label"] == "Project"
    assert body["label_plural"] == "Projects"
    assert body["description"] == "User-facing projects."
    assert body["list_display"] == ["id", "name"]
    assert body["search_fields"] == ["name", "summary"]
    assert body["ordering"] == ["name"]
    assert body["crud_actions"] == ["list", "read", "create", "update", "delete"]


def test_resource_contract_field_metadata(client):
    body = client.get("/api/v1/admin/_contract/projects").json()
    by_name = {f["name"]: f for f in body["fields"]}

    assert "id" in by_name
    assert by_name["id"]["primary_key"] is True
    assert by_name["id"]["read_only"] is True

    assert by_name["name"]["nullable"] is False
    assert by_name["summary"]["nullable"] is True

    # Calculated field is exposed read-only
    assert "display_name" in by_name
    assert by_name["display_name"]["calculated"] is True
    assert by_name["display_name"]["read_only"] is True


def test_resource_contract_omits_hidden_fields(client):
    body = client.get("/api/v1/admin/_contract/projects").json()
    names = [f["name"] for f in body["fields"]]
    # Globally protected
    assert "hashed_password" not in names
    # Per-admin protected
    assert "internal_token" not in names


def test_resource_contract_unknown_resource_returns_404(client):
    resp = client.get("/api/v1/admin/_contract/does-not-exist")
    assert resp.status_code == 404


def test_resource_contract_invalid_resource_name_returns_404(client):
    """Malformed resource names must fall through to 404 instead of leaking
    a validation 422 from the registry layer."""
    resp = client.get("/api/v1/admin/_contract/Invalid%20Name!")
    assert resp.status_code == 404


def test_resource_contract_path_traversal_returns_404(client):
    resp = client.get("/api/v1/admin/_contract/..%2Fadmin")
    assert resp.status_code == 404


def test_resource_contract_requires_authentication(app):
    app.dependency_overrides.clear()
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/api/v1/admin/_contract/projects")
    assert resp.status_code == 401


def test_builtin_admin_contract_loadable(client):
    body = client.get("/api/v1/admin/_contract/tenant_roles").json()
    assert body["resource"] == "tenant_roles"
    assert body["label"] == "Tenant Role"
    # tenant_roles has a `name` column — make sure it surfaced
    names = [f["name"] for f in body["fields"]]
    assert "name" in names
    assert "description" in names


def test_admin_actions_default_empty(client):
    body = client.get("/api/v1/admin/_contract/projects").json()
    assert body["admin_actions"] == []
