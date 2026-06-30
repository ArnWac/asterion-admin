"""G13 — systematic IDOR / cross-tenant-mutation coverage (PostgreSQL).

``test_http_tenant_isolation`` proves a foreign tenant cannot *read* another
tenant's row (GET → 404). This closes the rest of the IDOR surface on the CRUD
path: a foreign tenant must not be able to **mutate** (PATCH) or **delete**
(DELETE) a row it can't see either — every verb on a foreign-tenant id must
return 404, never 403 (a 403 would confirm the row exists; 404 leaks nothing)
and never silently succeed.

The auth gate is overridden with a superadmin holding ``admin.*`` whose tenant
mirrors the request's resolved tenant — so the *only* thing standing between the
caller and the foreign row is the ``search_path`` isolation, which is exactly
what must hold. Runs only when ``ASTERION_TEST_POSTGRES_URL`` is set.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest
from fastapi import Request
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from asterion import CoreAdminConfig, ModelAdmin, create_admin
from asterion.admin import require_admin_context
from asterion.admin.context import AdminContext
from asterion.models.base import TenantModel
from asterion.models.tenant import Tenant
from asterion.providers.base import AdminPrincipal, AdminTenant
from asterion.tenancy.resolver import clear_tenant_cache

pytestmark = pytest.mark.postgres

RESOURCE = "idor_widgets"


class IdorWidget(TenantModel):
    """Tenant-local model used only by the IDOR sweep. Distinct table from the
    isolation test's ``iso_widgets`` so both register cleanly in
    ``TenantBase.metadata`` and the ``pg_schemas`` fixture creates both."""

    __tablename__ = RESOURCE

    name: Mapped[str] = mapped_column(String(200), nullable=False)


class IdorWidgetAdmin(ModelAdmin):
    model = IdorWidget
    list_display = ["id", "name"]
    readonly_fields = ["id", "created_at", "updated_at"]


def _build_app(url: str):
    app = create_admin(
        config=CoreAdminConfig(
            database_url=url,
            secret_key="test-idor-secret",
            enable_multi_tenant=True,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        ),
        register=lambda reg: reg.register(IdorWidgetAdmin),
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
    suffix = uuid.uuid4().hex[:8]
    slug_a, slug_b = f"idor-a-{suffix}", f"idor-b-{suffix}"
    async with pg_sessionmaker() as session:
        async with session.begin():
            session.add(Tenant(name="A", slug=slug_a, schema_name=schema_a, is_active=True))
            session.add(Tenant(name="B", slug=slug_b, schema_name=schema_b, is_active=True))
    clear_tenant_cache()
    return slug_a, slug_b


async def _create_under(client, slug: str, name: str) -> str:
    resp = await client.post(
        f"/api/v1/admin/{RESOURCE}", json={"name": name}, headers={"X-Tenant-Slug": slug}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", ["get", "patch", "delete"])
async def test_foreign_tenant_record_is_404_for_every_verb(verb, pg_schemas, pg_sessionmaker):
    """A row created under tenant A must be 404 (not 403, not 200) for GET,
    PATCH and DELETE issued under tenant B."""
    url = os.environ["ASTERION_TEST_POSTGRES_URL"]
    slug_a, slug_b = await _seed_tenants(pg_sessionmaker, pg_schemas["a"], pg_schemas["b"])
    app = _build_app(url)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        widget_id = await _create_under(client, slug_a, "owned-by-A")

        path = f"/api/v1/admin/{RESOURCE}/{widget_id}"
        hdr = {"X-Tenant-Slug": slug_b}
        if verb == "get":
            resp = await client.get(path, headers=hdr)
        elif verb == "patch":
            resp = await client.patch(path, json={"name": "hijacked"}, headers=hdr)
        else:
            resp = await client.delete(path, headers=hdr)

        assert resp.status_code == 404, f"{verb} on foreign record: {resp.status_code} {resp.text}"

        # The row is untouched: tenant A still reads the original value, and the
        # delete did not go through.
        check = await client.get(path, headers={"X-Tenant-Slug": slug_a})
        assert check.status_code == 200, check.text
        assert check.json()["name"] == "owned-by-A"


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", ["get", "patch", "delete"])
async def test_nonexistent_id_is_404_for_every_verb(verb, pg_schemas, pg_sessionmaker):
    """A random (never-created) id must 404 for every verb within a valid
    tenant — the within-tenant IDOR baseline."""
    url = os.environ["ASTERION_TEST_POSTGRES_URL"]
    slug_a, _ = await _seed_tenants(pg_sessionmaker, pg_schemas["a"], pg_schemas["b"])
    app = _build_app(url)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        path = f"/api/v1/admin/{RESOURCE}/{uuid.uuid4()}"
        hdr = {"X-Tenant-Slug": slug_a}
        if verb == "get":
            resp = await client.get(path, headers=hdr)
        elif verb == "patch":
            resp = await client.patch(path, json={"name": "x"}, headers=hdr)
        else:
            resp = await client.delete(path, headers=hdr)
        assert resp.status_code == 404, f"{verb} on missing id: {resp.status_code} {resp.text}"


@pytest.mark.asyncio
async def test_owner_tenant_can_mutate_its_own_record(pg_schemas, pg_sessionmaker):
    """Positive control: the 404s above are isolation, not a broken mutation
    path — the owning tenant can PATCH and DELETE its own row."""
    url = os.environ["ASTERION_TEST_POSTGRES_URL"]
    slug_a, _ = await _seed_tenants(pg_sessionmaker, pg_schemas["a"], pg_schemas["b"])
    app = _build_app(url)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        widget_id = await _create_under(client, slug_a, "owned")
        path = f"/api/v1/admin/{RESOURCE}/{widget_id}"
        hdr = {"X-Tenant-Slug": slug_a}

        patched = await client.patch(path, json={"name": "renamed"}, headers=hdr)
        assert patched.status_code == 200, patched.text
        assert patched.json()["name"] == "renamed"

        deleted = await client.delete(path, headers=hdr)
        assert deleted.status_code in (200, 204), deleted.text

        gone = await client.get(path, headers=hdr)
        assert gone.status_code == 404, gone.text
