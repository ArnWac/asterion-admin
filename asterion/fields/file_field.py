"""``FileField`` column type + adapter (Roadmap P4.2).

Marks a column as holding a storage *key* — an opaque pointer the
framework's :class:`StorageBackend` resolves to bytes. The column itself
is a ``String`` under the hood, so any backend (SQLite/PostgreSQL/MySQL)
stores it natively; the :class:`FileFieldType` decorator only carries
the *identity* the :class:`FileFieldAdapter` needs to recognise the
column at admin-introspection time.

Wire shape
----------

* On serialize, the adapter emits the raw key string (or ``None``).
  Constructing a download URL from the key is a UI concern — the
  framework ships ``/api/v1/storage/{key}`` as the canonical serve
  route (P4.4); cloud backends that expose ``signed_url`` will get an
  optimisation pass later.
* On parse, the adapter accepts the same: a key string (returned by
  ``POST /storage/upload``) or ``None`` to clear. No transformation —
  the upload route owns key minting, the field just stores it.

Why a TypeDecorator instead of a marker column option
-----------------------------------------------------

Detection has to be unambiguous: ``StringAdapter`` is the universal
fallback, so any column whose type is a plain ``String`` will be
claimed by it. A dedicated subclass lets ``supports`` answer "yes,
file" with a single ``isinstance`` check that can't be shadowed by
String-likes (Enum/Text already use the same pattern).
"""

from __future__ import annotations

from typing import Any

import sqlalchemy.types as sqltypes
from sqlalchemy import Column

from asterion.fields.base import FieldContract


class FileFieldType(sqltypes.TypeDecorator):
    """SQLAlchemy column type for a storage-backed file reference.

    Stored value is a string key (e.g. ``"articles/2026/cover.png"``).
    ``length`` defaults to 1024 — enough for keys with deep prefixes,
    bucket tags, and UUID-derived names without forcing TEXT-class
    storage on engines that distinguish.
    """

    impl = sqltypes.String
    cache_ok = True

    def __init__(self, length: int = 1024, **kwargs: Any) -> None:
        super().__init__(length=length, **kwargs)


class FileFieldAdapter:
    """Recognises :class:`FileFieldType` columns and emits ``type="file"``.

    Must register **before** :class:`StringAdapter` because
    :class:`FileFieldType` extends ``String`` (the universal fallback
    would otherwise claim it).
    """

    name = "file"

    def supports(self, model_attr: Any) -> bool:
        return isinstance(model_attr, Column) and isinstance(model_attr.type, FileFieldType)

    def build_contract(self, model_attr: Column, ctx: Any | None = None) -> FieldContract:
        return FieldContract(
            name=model_attr.name,
            type="file",
            primary_key=bool(model_attr.primary_key),
            read_only=bool(model_attr.primary_key),
            hidden=False,
            nullable=bool(model_attr.nullable),
            calculated=False,
            python_type=str,
            metadata={"widget": "file"},
        )

    def serialize(self, value: Any, ctx: Any | None = None) -> Any:
        # The stored value is already the storage key — pass through.
        # URL construction happens in the UI from a known prefix; the
        # adapter stays pure (no runtime / no I/O) so unit tests for
        # admins with FileField don't need a wired storage backend.
        return value

    def parse(self, value: Any, ctx: Any | None = None) -> Any:
        # Accept the raw key as returned by POST /storage/upload, or
        # None to clear the reference. The upload route is the only
        # surface that mints new keys; the field never accepts raw
        # bytes here.
        return value


DEFAULT_FILE_ADAPTERS: tuple[type, ...] = (FileFieldAdapter,)
