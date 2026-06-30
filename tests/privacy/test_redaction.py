"""G7 — PII-aware redaction of audit ``changes``."""

from __future__ import annotations

from asterion.audit.service import audit_payload
from asterion.privacy import PIICategory, PIIFieldRegistry
from asterion.privacy.redaction import (
    REDACTED_PII,
    SUPPRESSED_BEHAVIORAL,
    get_default_audit_pii_mode,
    redact_pii,
    set_default_audit_pii_mode,
    suppress_behavioral,
)


def test_redact_masks_classified_fields_only():
    out = redact_pii(
        {"email": "alice@example.com", "full_name": "Alice", "widget_count": 7},
        mode="redact",
    )
    assert out["email"] == REDACTED_PII
    assert out["full_name"] == REDACTED_PII
    # Non-PII fields keep their value.
    assert out["widget_count"] == 7


def test_hash_mode_is_stable_and_opaque():
    a = redact_pii({"email": "alice@example.com"}, mode="hash")["email"]
    b = redact_pii({"email": "alice@example.com"}, mode="hash")["email"]
    c = redact_pii({"email": "bob@example.com"}, mode="hash")["email"]
    assert a == b  # equal values correlate
    assert a != c  # different values diverge
    assert "alice@example.com" not in a
    assert a.startswith("pii:sha256:")


def test_keep_mode_is_passthrough():
    payload = {"email": "alice@example.com", "n": 1}
    assert redact_pii(payload, mode="keep") == payload


def test_none_value_passes_through():
    assert redact_pii({"email": None}, mode="redact") == {"email": None}


def test_non_mapping_passthrough():
    assert redact_pii("nope", mode="redact") == "nope"


def test_default_mode_applies_when_mode_is_none():
    original = get_default_audit_pii_mode()
    try:
        set_default_audit_pii_mode("hash")
        out = redact_pii({"email": "alice@example.com"})
        assert out["email"].startswith("pii:sha256:")
    finally:
        set_default_audit_pii_mode(original)


def test_audit_payload_masks_pii_by_default():
    # The default mode is "redact", so the audit row must not carry raw PII.
    row = audit_payload(
        action="crud_update",
        resource="users",
        changes={"email": "alice@example.com", "full_name": "Alice", "age": 30},
    )
    assert row.changes is not None
    assert row.changes["email"] == REDACTED_PII
    assert row.changes["full_name"] == REDACTED_PII
    assert row.changes["age"] == 30


def _registry_with_behavioral() -> PIIFieldRegistry:
    reg = PIIFieldRegistry()
    reg.register("punch_time", PIICategory.BEHAVIORAL)
    return reg


def test_suppress_behavioral_masks_values_by_default():
    reg = _registry_with_behavioral()
    out = suppress_behavioral({"punch_time": "08:59", "note": "ok"}, detail=False, registry=reg)
    assert out["punch_time"] == SUPPRESSED_BEHAVIORAL
    assert out["note"] == "ok"


def test_suppress_behavioral_keeps_values_on_opt_in():
    reg = _registry_with_behavioral()
    out = suppress_behavioral({"punch_time": "08:59"}, detail=True, registry=reg)
    assert out["punch_time"] == "08:59"


def test_redact_pii_leaves_behavioral_untouched():
    # G7 must NOT mask BEHAVIORAL — that is G5's job, so the opt-in stays
    # meaningful. redact_pii leaves it as-is.
    reg = _registry_with_behavioral()
    out = redact_pii({"punch_time": "08:59"}, mode="redact", registry=reg)
    assert out["punch_time"] == "08:59"


def test_suppress_behavioral_none_passthrough():
    reg = _registry_with_behavioral()
    assert suppress_behavioral({"punch_time": None}, detail=False, registry=reg) == {
        "punch_time": None
    }


def test_audit_payload_suppresses_behavioral_by_default():
    from asterion.privacy import get_pii_registry, reset_for_tests

    reset_for_tests()
    try:
        get_pii_registry().register("punch_time", PIICategory.BEHAVIORAL)
        row = audit_payload(
            action="crud_update",
            resource="time_entries",
            changes={"punch_time": "08:59", "minutes": 30},
        )
        assert row.changes is not None
        assert row.changes["punch_time"] == SUPPRESSED_BEHAVIORAL
        assert row.changes["minutes"] == 30
    finally:
        reset_for_tests()


def test_audit_payload_redacts_secrets_before_pii():
    # Secret stripping (sanitize) and PII redaction compose: a password is
    # ***REDACTED***, an email is ***PII***.
    row = audit_payload(
        action="crud_update",
        resource="users",
        changes={"password": "s3cret", "email": "a@b.c"},
    )
    assert row.changes is not None
    assert row.changes["password"] == "***REDACTED***"
    assert row.changes["email"] == REDACTED_PII
