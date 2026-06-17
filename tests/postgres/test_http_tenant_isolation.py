"""End-to-end tenant isolation through the HTTP CRUD path (Review R2).

The other postgres tests prove the ``SET LOCAL search_path`` *primitive* on
hand-built sessions. They do NOT exercise the real request path, where the
search_path has to be applied to the request-scoped CRUD session by
``get_async_session`` (Review R1). This test closes that gap: it drives a full
``create_admin`` app over ASGI, registers a tenant-local ``ModelAdmin``, and
asserts that a record written under tenant A is invisible under tenant B.

Runs only when ``ADMINFOUNDRY_TEST_POSTGRES_URL`` is set (see conftest).
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest
from fastapi import Request
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from adminfoundry import CoreAdminConfig, ModelAdmin, create_admin
from adminfoundry.admin import require_admin_context
from adminfoundry.admin.context import AdminContext
from adminfoundry.models.base import TenantModel
from adminfoundry.models.tenant import Tenant
from adminfoundry.providers.base import AdminPrincipal, AdminTenant
from adminfoundry.tenancy.resolver import clear_tenant_cache

pytestmark = pytest.mark.postgres

RESOURCE = "iso_widgets"


class IsoWidget(TenantModel):
    """Tenant-local model used only by this isolation test. Lives in
    ``TenantBase.metadata`` so the ``pg_schemas`` fixture creates its table
    inside each tenant schema."""

    __tablename__ = RESOURCE

    name: Mapped[str] = mapped_column(String(200), nullable=False)


class IsoWidgetAdmin(ModelAdmin):
    model = IsoWidget
    list_display = ["id", "name"]
    search_fields = ["name"]
    readonly_fields = ["id", "created_at", "updated_at"]


def _build_app(url: str):
    """A multi-tenant app exposing IsoWidget CRUD, with the admin-context
    auth gate replaced by a superadmin whose tenant mirrors whatever the real
    ``TenantMiddleware`` resolved for the request. The data path
    (``get_async_session`` + middleware) runs for real — that's what R1/R2
    exercise."""
    app = create_admin(
        config=CoreAdminConfig(
            database_url=url,
            secret_key="test-iso-secret",
            enable_multi_tenant=True,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        ),
        register=lambda reg: reg.register(IsoWidgetAdmin),
    )

    async def _ctx_override(request: Request) -> AdminContext:
        t = getattr(request.state, "tenant", None)
        admin_tenant = (
            AdminTenant(id=str(t.id), slug=t.slug, name=t.name) if t is not None else None
        )
        return AdminContext(
            request=request,
            principal=AdminPrincipal(
                id="00000000-0000-0000-0000-0000000000aa",
                email="root@example.com",
                display_name="Root",
                is_active=True,
                is_superadmin=True,
            ),
            tenant=admin_tenant,
            permissions=frozenset({"admin.*"}),
        )

    app.dependency_overrides[require_admin_context] = _ctx_override
    return app


async def _seed_tenants(pg_sessionmaker, schema_a: str, schema_b: str) -> tuple[str, str]:
    """Insert two public.tenants rows pointing at the prepared schemas.
    Returns their slugs."""
    suffix = uuid.uuid4().hex[:8]
    slug_a, slug_b = f"iso-a-{suffix}", f"iso-b-{suffix}"
    async with pg_sessionmaker() as session:
        async with session.begin():
            session.add(Tenant(name="A", slug=slug_a, schema_name=schema_a, is_active=True))
            session.add(Tenant(name="B", slug=slug_b, schema_name=schema_b, is_active=True))
    clear_tenant_cache()
    return slug_a, slug_b


@pytest.mark.asyncio
async def test_record_written_under_one_tenant_is_invisible_to_the_other(
    pg_schemas, pg_sessionmaker
):
    url = os.environ["ADMINFOUNDRY_TEST_POSTGRES_URL"]
    slug_a, slug_b = await _seed_tenants(pg_sessionmaker, pg_schemas["a"], pg_schemas["b"])
    app = _build_app(url)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Create under tenant A.
        created = await client.post(
            f"/api/v1/admin/{RESOURCE}",
            json={"name": "from-A"},
            headers={"X-Tenant-Slug": slug_a},
        )
        assert created.status_code == 201, created.text
        widget_id = created.json()["id"]

        # Tenant A sees exactly its one row.
        list_a = await client.get(f"/api/v1/admin/{RESOURCE}", headers={"X-Tenant-Slug": slug_a})
        assert list_a.status_code == 200, list_a.text
        body_a = list_a.json()
        assert body_a["total"] == 1
        assert [w["name"] for w in body_a["items"]] == ["from-A"]

        # Tenant B sees NOTHING — the row lives in schema A only.
        list_b = await client.get(f"/api/v1/admin/{RESOURCE}", headers={"X-Tenant-Slug": slug_b})
        assert list_b.status_code == 200, list_b.text
        assert list_b.json()["total"] == 0

        # The row is not even addressable by id from tenant B.
        read_b = await client.get(
            f"/api/v1/admin/{RESOURCE}/{widget_id}", headers={"X-Tenant-Slug": slug_b}
        )
        assert read_b.status_code == 404, read_b.text

        # But it is from tenant A.
        read_a = await client.get(
            f"/api/v1/admin/{RESOURCE}/{widget_id}", headers={"X-Tenant-Slug": slug_a}
        )
        assert read_a.status_code == 200, read_a.text


@pytest.mark.asyncio
async def test_each_tenant_lists_only_its_own_rows(pg_schemas, pg_sessionmaker):
    url = os.environ["ADMINFOUNDRY_TEST_POSTGRES_URL"]
    slug_a, slug_b = await _seed_tenants(pg_sessionmaker, pg_schemas["a"], pg_schemas["b"])
    app = _build_app(url)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for slug, name in ((slug_a, "alpha"), (slug_a, "alpha-2"), (slug_b, "beta")):
            resp = await client.post(
                f"/api/v1/admin/{RESOURCE}",
                json={"name": name},
                headers={"X-Tenant-Slug": slug},
            )
            assert resp.status_code == 201, resp.text

        list_a = await client.get(f"/api/v1/admin/{RESOURCE}", headers={"X-Tenant-Slug": slug_a})
        assert {w["name"] for w in list_a.json()["items"]} == {"alpha", "alpha-2"}

        list_b = await client.get(f"/api/v1/admin/{RESOURCE}", headers={"X-Tenant-Slug": slug_b})
        assert {w["name"] for w in list_b.json()["items"]} == {"beta"}
