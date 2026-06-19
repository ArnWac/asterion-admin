"""Unit tests for the Google OIDC claim mapper.

The mapper is the only place that knows Google's specific claim names,
so it's also where the contract test lives. Fixtures use realistic
shapes — the dict keys + types match what Google's ID-token actually
contains (verified against
https://developers.google.com/identity/openid-connect/openid-connect
sample tokens).
"""

from __future__ import annotations

import pytest

from asterion.extensions.auth_oauth import GoogleOIDCClaimMapper
from asterion.extensions.auth_oauth.base import InvalidClaimsError


def _google_claims(**overrides):
    """A typical successful Google OIDC ID-token payload."""
    base = {
        "iss": "https://accounts.google.com",
        "azp": "1234567890.apps.googleusercontent.com",
        "aud": "1234567890.apps.googleusercontent.com",
        "sub": "108743263456789012345",  # opaque, stable, 21 digits
        "email": "alice@example.com",
        "email_verified": True,
        "name": "Alice Anderson",
        "given_name": "Alice",
        "family_name": "Anderson",
        "picture": "https://lh3.googleusercontent.com/a/abc=s96-c",
        "locale": "en",
        "iat": 1700000000,
        "exp": 1700003600,
    }
    base.update(overrides)
    return base


# --- success paths ---


def test_maps_sub_to_provider_subject():
    out = GoogleOIDCClaimMapper()(_google_claims())
    assert out.provider == "google"
    assert out.provider_subject == "108743263456789012345"


def test_maps_picture_to_picture_url():
    out = GoogleOIDCClaimMapper()(_google_claims())
    assert out.picture_url == "https://lh3.googleusercontent.com/a/abc=s96-c"


def test_maps_hd_to_hosted_domain():
    out = GoogleOIDCClaimMapper()(_google_claims(hd="acme.example"))
    assert out.hosted_domain == "acme.example"


def test_hosted_domain_absent_when_no_hd_claim():
    out = GoogleOIDCClaimMapper()(_google_claims())
    assert "hd" not in _google_claims()  # sanity on the fixture
    assert out.hosted_domain is None


def test_email_verified_bool_passthrough():
    out = GoogleOIDCClaimMapper()(_google_claims(email_verified=True))
    assert out.email_verified is True
    out2 = GoogleOIDCClaimMapper()(_google_claims(email_verified=False))
    assert out2.email_verified is False


def test_email_verified_string_true_is_normalized():
    """Some test/mock fixtures send strings — be defensive."""
    out = GoogleOIDCClaimMapper()(_google_claims(email_verified="true"))
    assert out.email_verified is True
    out2 = GoogleOIDCClaimMapper()(_google_claims(email_verified="false"))
    assert out2.email_verified is False


def test_email_verified_missing_becomes_none():
    claims = _google_claims()
    del claims["email_verified"]
    out = GoogleOIDCClaimMapper()(claims)
    assert out.email_verified is None


def test_all_typed_fields_optional_except_sub():
    out = GoogleOIDCClaimMapper()({"sub": "abc"})
    assert out.provider_subject == "abc"
    assert out.email_at_provider is None
    assert out.name is None
    assert out.picture_url is None
    assert out.hosted_domain is None
    assert out.raw_extra == {}


# --- failure path ---


def test_missing_sub_raises():
    claims = _google_claims()
    del claims["sub"]
    with pytest.raises(InvalidClaimsError, match="sub"):
        GoogleOIDCClaimMapper()(claims)


def test_empty_sub_raises():
    with pytest.raises(InvalidClaimsError, match="sub"):
        GoogleOIDCClaimMapper()(_google_claims(sub=""))


def test_non_string_sub_raises():
    with pytest.raises(InvalidClaimsError, match="sub"):
        GoogleOIDCClaimMapper()(_google_claims(sub=12345))


# --- raw_extra ---


def test_unknown_claims_land_in_raw_extra():
    out = GoogleOIDCClaimMapper()(_google_claims(custom_claim="foo", another=42))
    assert out.raw_extra["custom_claim"] == "foo"
    assert out.raw_extra["another"] == 42


def test_typed_claims_do_not_land_in_raw_extra():
    out = GoogleOIDCClaimMapper()(_google_claims())
    assert "sub" not in out.raw_extra
    assert "email" not in out.raw_extra
    assert "picture" not in out.raw_extra
    assert "hd" not in out.raw_extra


def test_standard_oidc_claims_iss_aud_iat_exp_in_raw_extra():
    """iss/aud/iat/exp aren't typed slots — they're for the token verifier,
    not the persisted identity. They land in raw_extra (transient)."""
    out = GoogleOIDCClaimMapper()(_google_claims())
    assert "iss" in out.raw_extra
    assert "aud" in out.raw_extra
    assert "iat" in out.raw_extra
    assert "exp" in out.raw_extra
