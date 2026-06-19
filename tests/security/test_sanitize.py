"""Tests for sanitize_payload secret redactor."""

from __future__ import annotations

from asterion.security.sanitize import REDACTED, sanitize_payload


def test_redacts_top_level_password():
    out = sanitize_payload({"email": "x@y.com", "password": "hunter2"})
    assert out["email"] == "x@y.com"
    assert out["password"] == REDACTED


def test_redacts_authorization_header():
    out = sanitize_payload({"Authorization": "Bearer abc"})
    assert out["Authorization"] == REDACTED


def test_redacts_set_cookie():
    out = sanitize_payload({"Set-Cookie": "session=abc"})
    assert out["Set-Cookie"] == REDACTED


def test_redacts_nested_secrets():
    payload = {
        "user": {
            "email": "x@y.com",
            "hashed_password": "$2b$abc",
            "tokens": {"access_token": "secret", "type": "bearer"},
        },
    }
    out = sanitize_payload(payload)
    assert out["user"]["email"] == "x@y.com"
    assert out["user"]["hashed_password"] == REDACTED
    assert out["user"]["tokens"]["access_token"] == REDACTED
    assert out["user"]["tokens"]["type"] == "bearer"


def test_redacts_inside_lists():
    payload = {"items": [{"api_key": "abc", "name": "first"}, {"name": "second"}]}
    out = sanitize_payload(payload)
    assert out["items"][0]["api_key"] == REDACTED
    assert out["items"][0]["name"] == "first"
    assert out["items"][1]["name"] == "second"


def test_preserves_non_sensitive_values():
    payload = {"count": 42, "active": True, "tags": ["a", "b"]}
    out = sanitize_payload(payload)
    assert out == payload


def test_partial_match_in_key_name():
    out = sanitize_payload({"user_password_hint": "blue"})
    assert out["user_password_hint"] == REDACTED


def test_case_insensitive_match():
    out = sanitize_payload({"PASSWORD": "x", "ApiKey": "y"})
    assert out["PASSWORD"] == REDACTED
    assert out["ApiKey"] == REDACTED


def test_non_mapping_inputs_passthrough():
    assert sanitize_payload("hello") == "hello"
    assert sanitize_payload(42) == 42
    assert sanitize_payload(None) is None


def test_does_not_mutate_input():
    original = {"password": "hunter2", "nested": {"token": "abc"}}
    sanitize_payload(original)
    assert original == {"password": "hunter2", "nested": {"token": "abc"}}
