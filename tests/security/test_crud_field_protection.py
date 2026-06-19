"""Tests for CRUD payload field-protection invariants.

These verify that hidden, read-only, calculated, and unknown fields
are correctly rejected on create/update, and that hidden fields never
appear in serializer output or contract metadata.
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


class _Base(DeclarativeBase):
    pass


class Account(_Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True)
    email = Column(String, nullable=False)
    hashed_password = Column(String, nullable=True)
    api_secret = Column(String, nullable=True)


class AccountAdmin(ModelAdmin):
    model = Account
    list_display = ["id", "email"]
    readonly_fields = ["id"]
    protected_fields = ["api_secret"]
    calculated_fields = {"display_name": lambda obj: f"Account {obj.id}"}


# --- write payload protection (MVP-S2) ---


def test_unknown_field_rejected():
    schema = build_model_schema(AccountAdmin())
    with pytest.raises(HTTPException) as exc:
        clean_write_payload({"email": "x@y.com", "unknown_field": 1}, schema, partial=False)
    assert exc.value.status_code == 422


def test_hidden_field_rejected_per_admin_protected():
    schema = build_model_schema(AccountAdmin())
    with pytest.raises(HTTPException) as exc:
        clean_write_payload({"email": "x@y.com", "api_secret": "leak"}, schema, partial=False)
    assert exc.value.status_code == 422


def test_globally_protected_field_rejected():
    schema = build_model_schema(AccountAdmin())
    with pytest.raises(HTTPException) as exc:
        clean_write_payload({"email": "x@y.com", "hashed_password": "leak"}, schema, partial=False)
    assert exc.value.status_code == 422


def test_readonly_field_rejected():
    schema = build_model_schema(AccountAdmin())
    with pytest.raises(HTTPException) as exc:
        clean_write_payload({"id": 99, "email": "x@y.com"}, schema, partial=False)
    assert exc.value.status_code == 422


def test_calculated_field_rejected_on_create():
    schema = build_model_schema(AccountAdmin())
    with pytest.raises(HTTPException) as exc:
        clean_write_payload({"email": "x@y.com", "display_name": "Mine"}, schema, partial=False)
    assert exc.value.status_code == 422


def test_calculated_field_rejected_on_update():
    schema = build_model_schema(AccountAdmin())
    with pytest.raises(HTTPException) as exc:
        clean_write_payload({"display_name": "Mine"}, schema, partial=True)
    assert exc.value.status_code == 422


def test_primary_key_write_rejected():
    schema = build_model_schema(AccountAdmin())
    with pytest.raises(HTTPException) as exc:
        clean_write_payload({"id": 5, "email": "x@y.com"}, schema, partial=False)
    assert exc.value.status_code == 422


def test_created_updated_at_writes_rejected_on_partial():
    """created_at/updated_at are DEFAULT_READONLY_FIELD_NAMES — rejected
    even when not declared on the admin."""
    schema = build_model_schema(AccountAdmin())
    with pytest.raises(HTTPException):
        clean_write_payload({"created_at": "2026-01-01"}, schema, partial=True)


def test_empty_create_payload_rejected():
    schema = build_model_schema(AccountAdmin())
    with pytest.raises(HTTPException) as exc:
        clean_write_payload({}, schema, partial=False)
    assert exc.value.status_code == 422


def test_clean_write_payload_strips_to_writable_only():
    schema = build_model_schema(AccountAdmin())
    cleaned = clean_write_payload({"email": "x@y.com"}, schema, partial=False)
    assert cleaned == {"email": "x@y.com"}


# --- serializer never exposes hidden fields ---


class _StubAccount:
    __table__ = Account.__table__

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_serializer_omits_globally_protected():
    obj = _StubAccount(
        id=1,
        email="x@y.com",
        hashed_password="$2b$leak",
        api_secret="topsecret",
    )
    out = serialize_record(obj, AccountAdmin())
    assert "hashed_password" not in out
    assert out["email"] == "x@y.com"


def test_serializer_omits_per_admin_protected():
    obj = _StubAccount(id=1, email="x@y.com", hashed_password=None, api_secret="topsecret")
    out = serialize_record(obj, AccountAdmin())
    assert "api_secret" not in out


def test_serializer_includes_calculated_field():
    obj = _StubAccount(id=42, email="x@y.com", hashed_password=None, api_secret=None)
    out = serialize_record(obj, AccountAdmin())
    assert out["display_name"] == "Account 42"


# --- contract never exposes hidden fields ---


def test_contract_excludes_hidden_fields():
    fields = build_field_metadata(AccountAdmin())
    names = [f.name for f in fields]
    assert "hashed_password" not in names
    assert "api_secret" not in names


def test_contract_marks_calculated_field_readonly():
    fields = build_field_metadata(AccountAdmin())
    calc = next(f for f in fields if f.name == "display_name")
    assert calc.calculated is True
    assert calc.read_only is True


def test_contract_marks_primary_key_readonly():
    contract = build_model_contract(AccountAdmin())
    id_field = next(f for f in contract.fields if f.name == "id")
    assert id_field.primary_key is True
    assert id_field.read_only is True
