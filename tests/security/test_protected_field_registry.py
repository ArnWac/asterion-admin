"""Tests for the central ProtectedFieldRegistry (Phase 4 v1-providers).

Proves the new registry-driven path:

* Defaults from ``DEFAULT_PROTECTED_FIELDS`` are honoured.
* Extension-style ``registry.register(...)`` calls flow into the
  ``ModelAdmin.all_protected`` set used by serializer + schema builder.
* Registered fields are stripped from serialized records, write payloads,
  and contract metadata exactly like ``hashed_password`` is.
* ``freeze()`` prevents further registration with a clear error.

Each test resets the singleton first so per-test additions don't leak.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import DeclarativeBase

from asterion.contract.service import build_field_metadata, build_model_contract
from asterion.crud.payload import clean_write_payload
from asterion.registry import ModelAdmin
from asterion.schemas.builder import build_model_schema
from asterion.schemas.serialization.serializer import serialize_record
from asterion.security.protected_fields import (
    DEFAULT_PROTECTED_FIELDS,
    ProtectedFieldRegistry,
    RegistryFrozenError,
    get_registry,
    reset_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    """Every test starts with a fresh, unfrozen registry."""
    reset_for_tests()
    yield
    reset_for_tests()


class _Base(DeclarativeBase):
    pass


class Widget(_Base):
    __tablename__ = "pf_widgets"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    # Field that extensions might want to protect at runtime
    external_token = Column(String, nullable=True)


class WidgetAdmin(ModelAdmin):
    model = Widget
    list_display = ["id", "name"]


class _StubWidget:
    __table__ = Widget.__table__

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


# --- Registry-level invariants ---


def test_default_seed_includes_hashed_password():
    assert "hashed_password" in get_registry()


def test_default_seed_matches_documented_defaults():
    assert get_registry().as_frozenset() == DEFAULT_PROTECTED_FIELDS


def test_register_adds_field():
    get_registry().register("external_token")
    assert "external_token" in get_registry()


def test_register_accepts_multiple_names():
    get_registry().register("access_token", "refresh_token", "id_token")
    snap = get_registry().as_frozenset()
    assert {"access_token", "refresh_token", "id_token"} <= snap


def test_register_rejects_empty_name():
    with pytest.raises(ValueError):
        get_registry().register("")


def test_register_rejects_non_string():
    with pytest.raises(ValueError):
        get_registry().register(123)  # type: ignore[arg-type]


def test_freeze_blocks_further_registration():
    reg = ProtectedFieldRegistry()
    reg.register("one")
    reg.freeze()
    assert reg.is_frozen is True
    with pytest.raises(RegistryFrozenError):
        reg.register("two")


# --- Registry-added fields flow into ModelAdmin.all_protected ---


def test_modeladmin_all_protected_includes_registry_default():
    admin = WidgetAdmin()
    assert "hashed_password" in admin.all_protected


def test_modeladmin_all_protected_picks_up_runtime_registration():
    """The whole point of Phase 4: extensions register a field, every
    consumer of ``all_protected`` sees it without code change."""
    get_registry().register("external_token")
    admin = WidgetAdmin()
    assert "external_token" in admin.all_protected


# --- End-to-end: serializer, schema builder, contract ---


def test_registry_field_is_stripped_from_serializer_output():
    get_registry().register("external_token")
    obj = _StubWidget(id=1, name="widget-a", external_token="leak-this")
    out = serialize_record(obj, WidgetAdmin())
    assert "external_token" not in out
    assert "leak-this" not in out.values()
    # Non-protected fields still flow through
    assert out["id"] == 1
    assert out["name"] == "widget-a"


def test_registry_field_is_rejected_on_write_payload():
    get_registry().register("external_token")
    schema = build_model_schema(WidgetAdmin())
    with pytest.raises(HTTPException) as exc:
        clean_write_payload({"name": "ok", "external_token": "leak"}, schema, partial=False)
    assert exc.value.status_code == 422


def test_registry_field_does_not_appear_in_contract():
    get_registry().register("external_token")
    field_names = [f.name for f in build_field_metadata(WidgetAdmin())]
    assert "external_token" not in field_names


def test_registry_field_does_not_appear_in_full_model_contract():
    get_registry().register("external_token")
    contract = build_model_contract(WidgetAdmin())
    names = [f.name for f in contract.fields]
    assert "external_token" not in names


# --- Deliberate global semantics (A1: pinned, not refactored) ---


def test_runtime_protected_fields_defaults_to_the_global_singleton():
    """``AdminRuntime.protected_fields`` deliberately defaults to the
    module-level singleton, so every app instance in a process shares
    one registry.

    This is a *documented* singleton (see runtime.py + protected_fields.py
    docstrings and docs/review-hardening-roadmap.md). Two apps sharing it can only ever
    over-protect (a field protected by app B is also stripped for app A),
    never leak — protected fields are a global, fail-safe concern. If a
    future change makes this per-runtime, it MUST also re-route every
    reader of ``ModelAdmin.all_protected``; this test is the tripwire.
    """
    import dataclasses

    from asterion.core.runtime import AdminRuntime

    pf_field = {f.name: f for f in dataclasses.fields(AdminRuntime)}["protected_fields"]
    assert pf_field.default_factory is not dataclasses.MISSING
    assert pf_field.default_factory() is get_registry()


def test_protected_field_is_shared_across_admin_instances():
    """A field registered once is honoured by every admin everywhere —
    the global-concern guarantee the read path relies on."""
    get_registry().register("external_token")
    assert "external_token" in WidgetAdmin().all_protected
    # A second, independently-constructed admin sees it too.
    assert "external_token" in WidgetAdmin().all_protected


# --- The default GLOBALLY_PROTECTED alias still works ---


def test_globally_protected_alias_still_exposes_defaults():
    """Existing imports of ``GLOBALLY_PROTECTED`` keep working (backwards
    compat alias for the documented seed set)."""
    from asterion.registry.admin import GLOBALLY_PROTECTED

    assert GLOBALLY_PROTECTED == DEFAULT_PROTECTED_FIELDS
    assert "hashed_password" in GLOBALLY_PROTECTED
