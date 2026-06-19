"""Tenant slug normalization (Review R12).

Both the write path (``validate_tenant_slug``) and the read path
(``_extract_slug``) canonicalize a slug to stripped-lowercase so a client's
casing / surrounding whitespace resolves the tenant that was stored in
canonical form. Genuinely malformed slugs still raise.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from asterion.security.validation import InvalidTenantSlugError, validate_tenant_slug
from asterion.tenancy.resolver import _extract_slug, _normalize_slug


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("acme", "acme"),
        ("Acme", "acme"),
        ("  ACME  ", "acme"),
        ("Foo-Bar", "foo-bar"),
    ],
)
def test_validate_tenant_slug_normalizes(raw, expected):
    assert validate_tenant_slug(raw) == expected


@pytest.mark.parametrize("bad", ["Invalid Slug!", "a", "1abc", "has space"])
def test_validate_tenant_slug_still_rejects_malformed(bad):
    with pytest.raises(InvalidTenantSlugError):
        validate_tenant_slug(bad)


def test_normalize_slug_helper():
    assert _normalize_slug("Acme") == "acme"
    assert _normalize_slug("  acme ") == "acme"
    assert _normalize_slug("") is None
    assert _normalize_slug("   ") is None
    assert _normalize_slug(None) is None


def _fake_request(headers: dict[str, str]):
    # No runtime on app.state → resolver falls back to header strategy +
    # the default X-Tenant-Slug header name.
    return SimpleNamespace(headers=headers, app=SimpleNamespace(state=SimpleNamespace()))


def test_extract_slug_normalizes_header_value():
    assert _extract_slug(_fake_request({"X-Tenant-Slug": "Acme"})) == "acme"
    assert _extract_slug(_fake_request({"X-Tenant-Slug": "  acme "})) == "acme"
    assert _extract_slug(_fake_request({})) is None
