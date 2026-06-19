"""Schema source of truth for the RBAC lookup (local review #1).

The request CRUD session scopes by ``tenant.schema_name`` (the DB column); the
permission lookup must use the SAME schema rather than re-deriving
``tenant_<slug>``. ``AdminTenant`` now carries ``schema_name`` and the builtin
tenant provider propagates it; ``_schema_for`` prefers it with a slug-derived
fallback for external providers.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from asterion.providers.base import AdminTenant
from asterion.providers.permissions import _schema_for
from asterion.providers.tenants import BuiltinTenantProvider


def test_schema_for_prefers_explicit_schema_name():
    t = AdminTenant(id="1", slug="acme", schema_name="tenant_custom_x")
    assert _schema_for(t) == "tenant_custom_x"


def test_schema_for_falls_back_to_slug_when_absent():
    t = AdminTenant(id="1", slug="acme")
    assert _schema_for(t) == "tenant_acme"


@pytest.mark.asyncio
async def test_builtin_provider_propagates_schema_name():
    ctx = SimpleNamespace(id="t1", slug="acme", name="Acme", schema_name="tenant_acme_42")
    request = SimpleNamespace(state=SimpleNamespace(tenant=ctx))
    tenant = await BuiltinTenantProvider().resolve_tenant(request)
    assert tenant is not None
    assert tenant.schema_name == "tenant_acme_42"
    # And the RBAC lookup would scope to that exact schema, matching the CRUD
    # session (no re-derivation from slug).
    assert _schema_for(tenant) == "tenant_acme_42"
