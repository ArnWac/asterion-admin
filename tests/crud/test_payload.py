"""Tests for CRUD payload validation."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from asterion.crud.payload import clean_write_payload
from asterion.schemas.fields import AdminModelSchema, FieldInfo


def _schema(*fields):
    return AdminModelSchema(model_name="test", fields=list(fields))


def _field(name, *, hidden=False, read_only=False, primary_key=False):
    return FieldInfo(name=name, primary_key=primary_key, hidden=hidden, read_only=read_only)


def test_clean_payload_basic():
    schema = _schema(
        _field("id", primary_key=True, read_only=True),
        _field("name"),
    )
    result = clean_write_payload({"name": "Alice"}, schema, partial=False)
    assert result == {"name": "Alice"}


def test_unknown_field_raises_422():
    schema = _schema(_field("name"))
    with pytest.raises(HTTPException) as exc:
        clean_write_payload({"name": "Alice", "extra": "val"}, schema, partial=False)
    assert exc.value.status_code == 422


def test_readonly_field_rejected():
    schema = _schema(
        _field("id", primary_key=True, read_only=True),
        _field("name"),
    )
    with pytest.raises(HTTPException) as exc:
        clean_write_payload({"id": "1", "name": "Alice"}, schema, partial=False)
    assert exc.value.status_code == 422


def test_hidden_field_rejected():
    schema = _schema(
        _field("password", hidden=True, read_only=True),
        _field("name"),
    )
    with pytest.raises(HTTPException):
        clean_write_payload({"password": "secret", "name": "Alice"}, schema, partial=False)


def test_empty_create_payload_raises():
    schema = _schema(_field("name"))
    with pytest.raises(HTTPException):
        clean_write_payload({}, schema, partial=False)


def test_partial_update_allows_empty():
    schema = _schema(_field("name"))
    result = clean_write_payload({}, schema, partial=True)
    assert result == {}
