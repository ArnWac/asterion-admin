"""Cross-tenant disclosure guard for the built-in member picker (v0.1.33).

Bug: ``TenantMembershipRoleAdmin``'s ``membership_id`` FK-options picker joined
``public.tenant_memberships`` → ``public.users`` with no tenant filter. Because
``tenant_memberships`` lives in the *public* schema, the request session's
per-tenant ``search_path`` does NOT scope it — so the picker offered members of
*every* tenant (their email addresses) when editing a Tenant Membership Role.

Acceptance: with two tenants that each own a distinct member, the picker scoped
to tenant A returns ONLY A's member and never B's email.

Skipped automatically unless ``ASTERION_TEST_POSTGRES_URL`` is set.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from asterion.admin.context import AdminContext
from asterion.builtins.admin import TenantMembershipRoleAdmin
from asterion.models.tenant import Tenant
from asterion.models.tenant_membership import TenantMembership
from asterion.models.user import User
from asterion.providers.base import AdminTenant

pytestmark = pytest.mark.postgres


@pytest_asyncio.fixture
async def two_tenants_with_members(pg_schemas, pg_sessionmaker):
    """Seed two public tenants, each with one user + membership.

    Returns ``{"a": (tenant_id, email), "b": (tenant_id, email)}``.
    """
    seeded: dict[str, tuple[str, str]] = {}
    async with pg_sessionmaker() as session:
        async with session.begin():
            for key in ("a", "b"):
                email = f"{key}@example.com"
                user = User(email=email, hashed_password="x", is_active=True)
                tenant = Tenant(
                    slug=f"tn-{key}", name=key.upper(), schema_name=pg_schemas[key]
                )
                session.add_all([user, tenant])
                await session.flush()
                session.add(
                    TenantMembership(user_id=user.id, tenant_id=tenant.id, is_active=True)
                )
                seeded[key] = (str(tenant.id), email)
    return seeded


def _ctx(tenant_id: str, slug: str) -> AdminContext:
    return AdminContext(
        request=None,
        principal=None,
        tenant=AdminTenant(id=tenant_id, slug=slug),
    )


@pytest.mark.asyncio
async def test_picker_only_offers_active_tenant_members(
    two_tenants_with_members, pg_sessionmaker
):
    a_tenant_id, a_email = two_tenants_with_members["a"]
    _, b_email = two_tenants_with_members["b"]

    async with pg_sessionmaker() as session:
        opts = await TenantMembershipRoleAdmin().resolve_fk_options(
            "membership_id", session=session, ctx=_ctx(a_tenant_id, "tn-a")
        )

    labels = {o["label"] for o in opts}
    assert labels == {a_email}
    # The whole point: tenant B's member never leaks into tenant A's picker.
    assert b_email not in labels
