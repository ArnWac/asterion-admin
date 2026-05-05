import uuid
from datetime import datetime
from typing import Optional, Any
from pydantic import create_model, ConfigDict
from pydantic import BaseModel
from sqlalchemy import inspect as sa_inspect
import sqlalchemy.types as sqltypes

from coreAdmin_api.admin.model_admin import ModelAdmin, AUTO_FIELDS

# SQLAlchemy type → Python type mapping
_TYPE_MAP: list[tuple[type, type]] = [
    (sqltypes.Boolean, bool),
    (sqltypes.DateTime, datetime),
    (sqltypes.Float, float),
    (sqltypes.Numeric, float),
    (sqltypes.BigInteger, int),
    (sqltypes.SmallInteger, int),
    (sqltypes.Integer, int),
    (sqltypes.Text, str),
    (sqltypes.String, str),
]


def _sa_type_to_python(sa_type: Any) -> type:
    type_name = type(sa_type).__name__.lower()
    if "uuid" in type_name or "guid" in type_name:
        return uuid.UUID
    for sa_cls, py_cls in _TYPE_MAP:
        if isinstance(sa_type, sa_cls):
            return py_cls
    return str  # safe fallback


def _col_info(model_admin: ModelAdmin) -> list[tuple[str, type, bool]]:
    """Return (name, python_type, is_optional) for each column."""
    mapper = sa_inspect(model_admin.model)
    cols = []
    for col in mapper.columns:
        py_type = _sa_type_to_python(col.type)
        is_optional = bool(col.nullable or col.default is not None or col.server_default is not None)
        cols.append((col.name, py_type, is_optional))
    return cols


class SchemaBuilder:
    def __init__(self) -> None:
        self._cache: dict[str, type[BaseModel]] = {}

    # --- public API ---

    def build_list_schema(self, model_admin: ModelAdmin) -> type[BaseModel]:
        return self._cached(model_admin, "list", self._build_list)

    def build_detail_schema(self, model_admin: ModelAdmin) -> type[BaseModel]:
        return self._cached(model_admin, "detail", self._build_detail)

    def build_create_schema(self, model_admin: ModelAdmin) -> type[BaseModel]:
        return self._cached(model_admin, "create", self._build_create)

    def build_update_schema(self, model_admin: ModelAdmin) -> type[BaseModel]:
        return self._cached(model_admin, "update", self._build_update)

    # --- builders ---

    def _build_list(self, model_admin: ModelAdmin) -> type[BaseModel]:
        excluded = model_admin.all_protected
        allowed = set(model_admin.list_display) if model_admin.list_display else None

        fields: dict[str, Any] = {}
        for name, py_type, is_optional in _col_info(model_admin):
            if name in excluded:
                continue
            if allowed is not None and name not in allowed:
                continue
            fields[name] = (Optional[py_type], None) if is_optional else (py_type, ...)

        # id is always included unless explicitly protected
        if "id" not in fields and "id" not in excluded:
            fields["id"] = (uuid.UUID, ...)

        return create_model(
            f"{model_admin.model.__name__}ListSchema",
            __config__=ConfigDict(from_attributes=True),
            **fields,
        )

    def _build_detail(self, model_admin: ModelAdmin) -> type[BaseModel]:
        excluded = model_admin.all_protected
        fields: dict[str, Any] = {}
        for name, py_type, is_optional in _col_info(model_admin):
            if name in excluded:
                continue
            fields[name] = (Optional[py_type], None) if is_optional else (py_type, ...)
        return create_model(
            f"{model_admin.model.__name__}DetailSchema",
            __config__=ConfigDict(from_attributes=True),
            **fields,
        )

    def _build_create(self, model_admin: ModelAdmin) -> type[BaseModel]:
        excluded = (
            model_admin.all_protected
            | AUTO_FIELDS
            | frozenset(model_admin.readonly_fields)
        )
        fields: dict[str, Any] = {}
        for name, py_type, is_optional in _col_info(model_admin):
            if name in excluded:
                continue
            fields[name] = (Optional[py_type], None) if is_optional else (py_type, ...)
        # Virtual fields only used at create time (e.g. plain-text password)
        for vname, vtype in model_admin.extra_create_fields.items():
            fields[vname] = (vtype, ...)
        return create_model(
            f"{model_admin.model.__name__}CreateSchema",
            __config__=ConfigDict(extra="forbid"),
            **fields,
        )

    def _build_update(self, model_admin: ModelAdmin) -> type[BaseModel]:
        excluded = (
            model_admin.all_protected
            | AUTO_FIELDS
            | frozenset(model_admin.readonly_fields)
        )
        fields: dict[str, Any] = {}
        for name, py_type, _is_optional in _col_info(model_admin):
            if name in excluded:
                continue
            # All update fields are optional
            fields[name] = (Optional[py_type], None)
        return create_model(
            f"{model_admin.model.__name__}UpdateSchema",
            __config__=ConfigDict(extra="forbid"),
            **fields,
        )

    def invalidate(self, model_name: str) -> None:
        """Remove cached schemas for a model (used when a ModelAdmin is replaced in tests)."""
        keys = [k for k in self._cache if k.startswith(f"{model_name}_")]
        for k in keys:
            del self._cache[k]

    # --- cache helper ---

    def _cached(
        self,
        model_admin: ModelAdmin,
        kind: str,
        builder: Any,
    ) -> type[BaseModel]:
        key = f"{model_admin.model_name}_{kind}"
        if key not in self._cache:
            self._cache[key] = builder(model_admin)
        return self._cache[key]


schema_builder = SchemaBuilder()
