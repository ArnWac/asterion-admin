from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, create_model
from sqlalchemy import inspect as sa_inspect

from asterion.fields import FieldRegistry, build_default_registry
from asterion.registry.admin import AUTO_FIELDS, ModelAdmin
from asterion.schemas.fields import AdminModelSchema, FieldInfo


def _col_info(
    model_admin: ModelAdmin,
    registry: FieldRegistry,
) -> list[tuple[str, type, bool]]:
    """Per-column ``(name, python_type, is_optional)`` triples.

    ``python_type`` is sourced from the field adapter that claims the
    column; ``is_optional`` follows the same rule as before — nullable
    columns or columns with any default count as optional in the
    write-side schema.
    """
    mapper = sa_inspect(model_admin.model)
    cols: list[tuple[str, type, bool]] = []
    for col in mapper.columns:
        adapter = registry.find_adapter(col)
        py_type: type = str
        if adapter is not None:
            contract = adapter.build_contract(col)
            if contract.python_type is not None:
                py_type = contract.python_type
        is_optional = bool(
            col.nullable or col.default is not None or col.server_default is not None
        )
        cols.append((col.name, py_type, is_optional))
    return cols


class SchemaBuilder:
    """Pydantic schema factory driven by the field registry.

    A3 replaces the previous inline ``_TYPE_MAP`` with a registry lookup.
    Construct one ``SchemaBuilder`` per app — the cache is per-instance
    and tied to the registry passed in at construction. ``runtime.fields``
    is the registry the framework uses internally; callers with a custom
    or extension-augmented registry build their own instance.
    """

    def __init__(self, registry: FieldRegistry | None = None) -> None:
        self._cache: dict[str, type[BaseModel]] = {}
        self._registry = registry or build_default_registry()

    def build_list_schema(self, model_admin: ModelAdmin) -> type[BaseModel]:
        return self._cached(model_admin, "list", self._build_list)

    def build_detail_schema(self, model_admin: ModelAdmin) -> type[BaseModel]:
        return self._cached(model_admin, "detail", self._build_detail)

    def build_create_schema(self, model_admin: ModelAdmin) -> type[BaseModel]:
        return self._cached(model_admin, "create", self._build_create)

    def build_update_schema(self, model_admin: ModelAdmin) -> type[BaseModel]:
        return self._cached(model_admin, "update", self._build_update)

    def _build_list(self, model_admin: ModelAdmin) -> type[BaseModel]:
        excluded = model_admin.all_protected
        allowed = set(model_admin.list_display) if model_admin.list_display else None

        fields: dict[str, Any] = {}
        for name, py_type, is_optional in _col_info(model_admin, self._registry):
            if name in excluded:
                continue
            if allowed is not None and name not in allowed:
                continue
            fields[name] = (py_type | None, None) if is_optional else (py_type, ...)

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
        for name, py_type, is_optional in _col_info(model_admin, self._registry):
            if name in excluded:
                continue
            fields[name] = (py_type | None, None) if is_optional else (py_type, ...)
        return create_model(
            f"{model_admin.model.__name__}DetailSchema",
            __config__=ConfigDict(from_attributes=True),
            **fields,
        )

    def _build_create(self, model_admin: ModelAdmin) -> type[BaseModel]:
        excluded = model_admin.all_protected | AUTO_FIELDS | frozenset(model_admin.readonly_fields)
        fields: dict[str, Any] = {}
        for name, py_type, is_optional in _col_info(model_admin, self._registry):
            if name in excluded:
                continue
            fields[name] = (py_type | None, None) if is_optional else (py_type, ...)
        return create_model(
            f"{model_admin.model.__name__}CreateSchema",
            __config__=ConfigDict(extra="forbid"),
            **fields,
        )

    def _build_update(self, model_admin: ModelAdmin) -> type[BaseModel]:
        excluded = model_admin.all_protected | AUTO_FIELDS | frozenset(model_admin.readonly_fields)
        fields: dict[str, Any] = {}
        for name, py_type, _is_optional in _col_info(model_admin, self._registry):
            if name in excluded:
                continue
            fields[name] = (py_type | None, None)
        return create_model(
            f"{model_admin.model.__name__}UpdateSchema",
            __config__=ConfigDict(extra="forbid"),
            **fields,
        )

    def invalidate(self, model_name: str) -> None:
        keys = [k for k in self._cache if k.startswith(f"{model_name}_")]
        for k in keys:
            del self._cache[k]

    def _cached(self, model_admin, kind, builder) -> type[BaseModel]:
        key = f"{model_admin.model_name}_{kind}"
        if key not in self._cache:
            self._cache[key] = builder(model_admin)
        return self._cache[key]


def build_model_schema(model_admin: ModelAdmin) -> AdminModelSchema:
    mapper = sa_inspect(model_admin.model)
    protected = model_admin.all_protected
    readonly_set = frozenset(model_admin.readonly_fields)

    fields: list[FieldInfo] = []
    for col in mapper.columns:
        is_pk = bool(col.primary_key)
        if col.name in protected:
            continue
        is_readonly = is_pk or col.name in readonly_set
        fields.append(
            FieldInfo(
                name=col.name,
                primary_key=is_pk,
                hidden=False,
                read_only=is_readonly,
            )
        )

    for fname in model_admin.calculated_fields:
        fields.append(
            FieldInfo(
                name=fname,
                primary_key=False,
                hidden=False,
                read_only=True,
            )
        )

    return AdminModelSchema(model_name=model_admin.model_name, fields=fields)
