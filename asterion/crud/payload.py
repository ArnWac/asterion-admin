from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import Date, DateTime, Time
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Mapper

from asterion.models.base import GUID
from asterion.schemas.fields import AdminModelSchema

DEFAULT_READONLY_FIELD_NAMES = frozenset(
    {
        "id",
        "created_at",
        "updated_at",
        "created_by",
        "updated_by",
        "deleted_at",
    }
)


def known_field_names(schema: AdminModelSchema) -> set[str]:
    return {field.name for field in schema.fields}


def writable_field_names(schema: AdminModelSchema) -> set[str]:
    return {
        field.name
        for field in schema.fields
        if not field.hidden
        and not field.read_only
        and not field.primary_key
        and field.name not in DEFAULT_READONLY_FIELD_NAMES
    }


def clean_write_payload(
    payload: Mapping[str, Any],
    schema: AdminModelSchema,
    *,
    partial: bool,
) -> dict[str, Any]:
    known_fields = known_field_names(schema)
    writable_fields = writable_field_names(schema)

    incoming_fields = set(payload.keys())

    unknown = incoming_fields - known_fields
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": "Payload contains unknown fields.",
                "fields": sorted(unknown),
            },
        )

    forbidden = incoming_fields - writable_fields
    if forbidden:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": "Payload contains non-writable fields.",
                "fields": sorted(forbidden),
            },
        )

    cleaned = {key: value for key, value in payload.items() if key in writable_fields}

    if not partial and not cleaned:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Create payload must contain at least one writable field.",
        )

    return cleaned


def validate_uuid_fields(cleaned: Mapping[str, Any], model: type) -> None:
    """Reject non-UUID values for GUID columns with a 422 (not a DB 500).

    The write path validates field *names* via :func:`clean_write_payload` but
    not column *types*, so a bad id (e.g. a free-text ``project_id`` of
    ``"test"``) would otherwise reach the driver and surface as a 500 from
    ``GUID.process_bind_param``. This catches the common case — UUID PKs/FKs —
    early and reports it as a field error.
    """
    mapper: Mapper[Any] = sa_inspect(model)
    columns = mapper.columns
    bad: list[str] = []
    for name, value in cleaned.items():
        if value is None or name not in columns:
            continue
        if not isinstance(columns[name].type, GUID):
            continue
        if isinstance(value, uuid.UUID):
            continue
        try:
            uuid.UUID(str(value))
        except (ValueError, AttributeError, TypeError):
            bad.append(name)
    if bad:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": "Invalid UUID value for field(s).", "fields": sorted(bad)},
        )


def _parse_datetime(text: str) -> dt.datetime | None:
    # ``datetime.fromisoformat`` parses the offset form natively on 3.11+; the
    # trailing-``Z`` military form is the one shape it still rejects, and it is
    # exactly what ``new Date(...).toISOString()`` (the admin UI) emits.
    candidate = f"{text[:-1]}+00:00" if text.endswith(("Z", "z")) else text
    try:
        return dt.datetime.fromisoformat(candidate)
    except ValueError:
        return None


def _parse_date(text: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        # Tolerate a full datetime string landing in a date column (e.g. a
        # datetime-local input wired to a ``sa.Date`` field) — keep the date.
        parsed = _parse_datetime(text)
        return parsed.date() if parsed is not None else None


def _parse_time(text: str) -> dt.time | None:
    try:
        return dt.time.fromisoformat(text)
    except ValueError:
        return None


def coerce_temporal_fields(cleaned: dict[str, Any], model: type) -> None:
    """Cast ISO-8601 strings to ``date``/``datetime``/``time`` in place.

    The admin UI legitimately sends date/datetime/time inputs as strings. Like
    :func:`validate_uuid_fields`, the write path validates field *names* but not
    column *types*, so an un-cast string would reach the driver and surface as a
    500 (PostgreSQL won't coerce ``character varying`` into ``date`` /
    ``timestamp`` / ``time`` — ``DatatypeMismatchError``). This parses the common
    case early; an unparseable string becomes a 422 field error, never a 500.

    Empty/whitespace-only strings (a cleared form field) become ``None``;
    non-string values (already-parsed objects from non-HTTP callers) pass
    through untouched.
    """
    mapper: Mapper[Any] = sa_inspect(model)
    columns = mapper.columns
    bad: list[str] = []
    for name, value in cleaned.items():
        if not isinstance(value, str) or name not in columns:
            continue
        col_type = columns[name].type
        if not isinstance(col_type, (Date, DateTime, Time)):
            continue
        text = value.strip()
        if not text:
            cleaned[name] = None  # a cleared form field
            continue
        parsed: dt.date | dt.time | None
        if isinstance(col_type, DateTime):  # not a subclass of Date — siblings
            parsed = _parse_datetime(text)
        elif isinstance(col_type, Date):
            parsed = _parse_date(text)
        else:
            parsed = _parse_time(text)
        if parsed is None:
            bad.append(name)
        else:
            cleaned[name] = parsed
    if bad:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": "Invalid date/time value for field(s).", "fields": sorted(bad)},
        )


def prepare_write_payload(
    payload: Mapping[str, Any],
    schema: AdminModelSchema,
    model: type,
    *,
    partial: bool,
) -> dict[str, Any]:
    """Clean a write payload and coerce its values to the column types.

    The single entry point every CRUD write path uses: validate field
    names/writability (:func:`clean_write_payload`), then turn the two classes
    of string input the driver can't coerce into 422-able errors or cast values
    (:func:`validate_uuid_fields`, :func:`coerce_temporal_fields`). Keeping the
    sequence in one place means a new write site can't apply the name-clean but
    silently forget a type pass.
    """
    cleaned = clean_write_payload(payload, schema, partial=partial)
    validate_uuid_fields(cleaned, model)
    coerce_temporal_fields(cleaned, model)
    return cleaned
