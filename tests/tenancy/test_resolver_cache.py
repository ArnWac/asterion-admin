"""Per-process tenant cache controls (Review R9).

Unit-level coverage of the cache primitives: targeted invalidation, full
clear, and the ``ttl <= 0`` "don't cache" path. Resolution itself is covered
by the middleware/resolver integration tests.
"""

from __future__ import annotations

import uuid

import pytest

from asterion.tenancy import resolver
from asterion.tenancy.context import TenantContext


def _ctx(slug: str) -> TenantContext:
    return TenantContext(
        id=uuid.uuid4(),
        slug=slug,
        name=slug.capitalize(),
        is_active=True,
        schema_name=f"tenant_{slug}",
    )


@pytest.fixture(autouse=True)
def _clean_cache():
    resolver.clear_tenant_cache()
    yield
    resolver.clear_tenant_cache()


def test_set_then_get_hits():
    resolver._mem_set("acme", _ctx("acme"), ttl=60)
    hit, ctx = resolver._mem_get("acme")
    assert hit is True
    assert ctx is not None and ctx.slug == "acme"


def test_invalidate_tenant_evicts_one_slug():
    resolver._mem_set("acme", _ctx("acme"), ttl=60)
    resolver._mem_set("beta", _ctx("beta"), ttl=60)
    resolver.invalidate_tenant("acme")
    assert resolver._mem_get("acme") == (False, None)
    # Other slugs are untouched.
    assert resolver._mem_get("beta")[0] is True


def test_invalidate_unknown_slug_is_noop():
    resolver.invalidate_tenant("never-cached")  # must not raise


def test_clear_tenant_cache_drops_everything():
    resolver._mem_set("acme", _ctx("acme"), ttl=60)
    resolver.clear_tenant_cache()
    assert resolver._mem_get("acme") == (False, None)


def test_zero_ttl_disables_caching():
    resolver._mem_set("acme", _ctx("acme"), ttl=0)
    assert resolver._mem_get("acme") == (False, None)


def test_negative_ttl_evicts_existing_entry():
    resolver._mem_set("acme", _ctx("acme"), ttl=60)
    # A subsequent non-caching write must not leave a stale entry behind.
    resolver._mem_set("acme", _ctx("acme"), ttl=0)
    assert resolver._mem_get("acme") == (False, None)
