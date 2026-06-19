"""Tests for the Serializer."""

from __future__ import annotations

import uuid

from sqlalchemy import Column, String
from sqlalchemy.orm import DeclarativeBase

from asterion.registry import ModelAdmin
from asterion.schemas.serialization.serializer import serialize_record, serialize_records


class _Base(DeclarativeBase):
    pass


class Note(_Base):
    __tablename__ = "notes"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String(200))
    body = Column(String)
    secret = Column(String, nullable=True)


class NoteAdmin(ModelAdmin):
    model = Note
    protected_fields = ["secret"]


class _FakeNote:
    __table__ = Note.__table__

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_serialize_record_excludes_protected():
    note = _FakeNote(id="1", title="Hello", body="World", secret="hidden")
    result = serialize_record(note, NoteAdmin())
    assert "secret" not in result
    assert result["title"] == "Hello"


def test_serialize_record_converts_uuid():
    uid = uuid.uuid4()
    note = _FakeNote(id=uid, title="Test", body="", secret=None)
    result = serialize_record(note, NoteAdmin())
    assert result["id"] == str(uid)


def test_serialize_records_returns_list():
    notes = [
        _FakeNote(id="1", title="A", body="", secret=None),
        _FakeNote(id="2", title="B", body="", secret=None),
    ]
    result = serialize_records(notes, NoteAdmin())
    assert len(result) == 2
    assert result[0]["title"] == "A"
