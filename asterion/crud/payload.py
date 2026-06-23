from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException, status
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
