from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import Date, DateTime, String, cast, func, inspect, or_, select
from sqlalchemy.orm import ColumnProperty
from sqlalchemy.sql import Select

from asterion.registry import ModelAdmin
from asterion.security.validation import (
    DEFAULT_PAGE_LIMIT as DEFAULT_LIMIT,
)
from asterion.security.validation import (
    validate_limit_offset,
)


def normalize_limit_offset(
    *,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> tuple[int, int]:
    return validate_limit_offset(limit=limit, offset=offset)


def primary_key_column(model: type[Any]):
    mapper = inspect(model)
    primary_key = mapper.primary_key

    if len(primary_key) != 1:
        raise RuntimeError(f"CRUD only supports models with exactly one primary key: {model!r}")

    return primary_key[0]


def coerce_primary_key_value(model: type[Any], value: str) -> Any:
    pk_column = primary_key_column(model)

    try:
        python_type = pk_column.type.python_type
    except NotImplementedError:
        python_type = str

    if python_type is int:
        try:
            return int(value)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Invalid integer primary key.",
            ) from exc

    if python_type is uuid.UUID:
        try:
            return uuid.UUID(value)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Invalid UUID primary key.",
            ) from exc

    return value


def model_column_names(model: type[Any]) -> set[str]:
    mapper = inspect(model)

    names: set[str] = set()

    for attr in mapper.attrs:
        if isinstance(attr, ColumnProperty):
            names.add(attr.key)

    return names


def get_model_column(model: type[Any], field_name: str):
    columns = inspect(model).columns

    if field_name not in columns:
        raise ValueError(f"Unknown column for model {model!r}: {field_name}")

    return columns[field_name]


def apply_ordering(
    stmt: Select,
    admin_class: type[ModelAdmin],
    ordering: str | None = None,
) -> Select:
    model = admin_class.model
    known_columns = model_column_names(model)
    pk = primary_key_column(model)

    # Request-supplied single-column ordering (Roadmap 5.5) overrides the
    # admin's static default. A ``-`` prefix means descending. An unknown
    # field name falls back to the static default rather than erroring, so
    # a stale client or hand-edited URL degrades gracefully.
    if ordering:
        descending = ordering.startswith("-")
        field_name = ordering[1:] if descending else ordering
        if field_name in known_columns:
            column = get_model_column(model, field_name)
            primary = column.desc() if descending else column.asc()
            # Append the PK as a stable tiebreaker so pagination is
            # deterministic when the sort column has duplicate values.
            if field_name == pk.name:
                return stmt.order_by(primary)
            return stmt.order_by(primary, pk.asc())

    ordering_cfg = tuple(getattr(admin_class, "ordering", ()) or ())

    if not ordering_cfg:
        return stmt.order_by(pk.asc())

    order_clauses = []

    for item in ordering_cfg:
        descending = item.startswith("-")
        field_name = item[1:] if descending else item

        if field_name not in known_columns:
            raise RuntimeError(
                f"{admin_class.__name__}.ordering contains unknown field: {field_name}"
            )

        column = get_model_column(model, field_name)
        order_clauses.append(column.desc() if descending else column.asc())

    return stmt.order_by(*order_clauses)


FILTER_PARAM_PREFIX = "filter_"


def parse_filter_query(
    query_params,
    admin_class: ModelAdmin,
) -> dict[str, Any]:
    """Extract ``filter_<field>`` entries from a request's query params.

    ``query_params`` is anything iterable as ``(key, value)`` —
    FastAPI's ``request.query_params`` works directly. Keys without
    the ``filter_`` prefix are ignored. Unknown filter fields (not on
    ``admin_class.filter_fields``) raise 422 so typos are loud."""
    allowed = set(getattr(admin_class, "filter_fields", []) or [])

    if hasattr(query_params, "multi_items"):
        items = list(query_params.multi_items())
    else:
        items = list(query_params)

    parsed: dict[str, Any] = {}
    unknown: list[str] = []
    for key, raw_value in items:
        if not key.startswith(FILTER_PARAM_PREFIX):
            continue
        field_name = key[len(FILTER_PARAM_PREFIX) :]
        if field_name not in allowed:
            unknown.append(field_name)
            continue
        parsed[field_name] = raw_value

    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": "Unknown or unfilterable field(s) in query.",
                "fields": sorted(set(unknown)),
            },
        )

    return parsed


def _coerce_filter_value(column, raw_value: str) -> Any:
    """Best-effort coercion of a query-string filter value to the
    column's python type.

    Booleans accept ``true``/``false``/``1``/``0`` (case-insensitive).
    Integers and UUIDs raise 422 on bad input. Strings / unknown
    column python types pass through verbatim — the SQL layer will
    do whatever Postgres / SQLite would do for a bare string.
    """
    try:
        py_type = column.type.python_type
    except NotImplementedError:
        return raw_value

    if py_type is bool:
        v = raw_value.strip().lower()
        if v in {"true", "1", "yes", "on"}:
            return True
        if v in {"false", "0", "no", "off"}:
            return False
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Filter value for {column.name!r} must be a boolean.",
        )

    if py_type is int:
        try:
            return int(raw_value)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Filter value for {column.name!r} must be an integer.",
            ) from exc

    if py_type is uuid.UUID:
        try:
            return uuid.UUID(raw_value)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Filter value for {column.name!r} must be a UUID.",
            ) from exc

    return raw_value


def apply_filters(
    stmt: Select,
    admin_class: ModelAdmin,
    filters: dict[str, Any],
) -> Select:
    """Apply parsed filters as ``column == value`` predicates.

    Empty filter dict → no-op (keeps the cost zero for callers that
    don't filter)."""
    if not filters:
        return stmt
    model = admin_class.model
    for field_name, raw_value in filters.items():
        column = get_model_column(model, field_name)
        coerced = _coerce_filter_value(column, str(raw_value))
        stmt = stmt.where(column == coerced)
    return stmt


def _parse_date_hierarchy(value: str) -> tuple[datetime, datetime] | None:
    """Parse ``YYYY`` / ``YYYY-MM`` / ``YYYY-MM-DD`` into a ``[start, end)``
    half-open datetime range. Returns ``None`` for anything unparseable so
    the caller can fall back to "no date filter" instead of erroring."""
    parts = value.split("-")
    try:
        if len(parts) == 1:
            year = int(parts[0])
            return datetime(year, 1, 1), datetime(year + 1, 1, 1)
        if len(parts) == 2:
            year, month = int(parts[0]), int(parts[1])
            if not 1 <= month <= 12:
                return None
            start = datetime(year, month, 1)
            end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
            return start, end
        if len(parts) == 3:
            year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
            start = datetime(year, month, day)
            return start, start + timedelta(days=1)
    except (ValueError, OverflowError):
        return None
    return None


def apply_date_hierarchy(
    stmt: Select,
    admin_class: type[ModelAdmin],
    value: str | None,
) -> Select:
    """Filter to a date period over ``ModelAdmin.date_hierarchy`` (Roadmap 5.5).

    No-op when the admin declares no ``date_hierarchy``, the request omits
    ``value``, the named column is missing, or ``value`` doesn't parse —
    each degrades to "no date filter" rather than erroring.
    """
    field = getattr(admin_class, "date_hierarchy", None)
    if not field or not value:
        return stmt
    if field not in model_column_names(admin_class.model):
        return stmt
    parsed = _parse_date_hierarchy(value)
    if parsed is None:
        return stmt
    start, end = parsed
    column = get_model_column(admin_class.model, field)
    # Match the column's granularity so Date columns compare cleanly.
    if isinstance(column.type, Date) and not isinstance(column.type, DateTime):
        lo, hi = start.date(), end.date()
    else:
        lo, hi = start, end
    return stmt.where(column >= lo, column < hi)


def apply_search(
    stmt: Select,
    admin_class: type[ModelAdmin],
    search: str | None,
) -> Select:
    if search is None or not search.strip():
        return stmt

    model = admin_class.model
    search_fields = tuple(getattr(admin_class, "search_fields", ()) or ())

    if not search_fields:
        return stmt

    known_columns = model_column_names(model)
    search_value = f"%{search.strip()}%"

    clauses = []

    for field_name in search_fields:
        if field_name not in known_columns:
            raise RuntimeError(
                f"{admin_class.__name__}.search_fields contains unknown field: {field_name}"
            )

        column = get_model_column(model, field_name)
        clauses.append(cast(column, String).ilike(search_value))

    if not clauses:
        return stmt

    return stmt.where(or_(*clauses))


def count_statement_for(stmt: Select) -> Select:
    return select(func.count()).select_from(stmt.order_by(None).limit(None).offset(None).subquery())
