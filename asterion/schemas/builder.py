from __future__ import annotations

from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Mapper

from asterion.registry.admin import ModelAdmin
from asterion.schemas.fields import AdminModelSchema, FieldInfo


def build_model_schema(model_admin: ModelAdmin) -> AdminModelSchema:
    mapper: Mapper[Any] = sa_inspect(model_admin.model)
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
