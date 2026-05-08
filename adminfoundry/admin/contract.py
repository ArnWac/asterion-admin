"""
Builds renderer-independent admin contract metadata from ModelAdmin configuration
and SQLAlchemy column introspection.  No ORM objects or backend internals are exposed.

Contract versioning policy (Phase 9):
- CONTRACT_VERSION major bump = backward-incompatible change (field removed/renamed,
  type changed, semantic altered).
- Same version = additive change only (new optional field added).
"""
from __future__ import annotations
import sqlalchemy.types as sqltypes
from sqlalchemy import inspect as sa_inspect

from adminfoundry.admin.model_admin import ModelAdmin
from adminfoundry.schemas.admin_contract import (
    ActionMeta,
    FieldMeta,
    InlineRelationMeta,
    ModelContractMeta,
    RelationMeta,
)
# policy_engine imported lazily to avoid circular import at module load


# Increment major on any backward-incompatible change to the public contract.
CONTRACT_VERSION = "1"


def _field_type(sa_type) -> str:
    type_name = type(sa_type).__name__.lower()
    if "uuid" in type_name or "guid" in type_name:
        return "uuid"
    if isinstance(sa_type, sqltypes.Boolean):
        return "boolean"
    if isinstance(sa_type, (sqltypes.BigInteger, sqltypes.SmallInteger, sqltypes.Integer)):
        return "integer"
    if isinstance(sa_type, (sqltypes.Float, sqltypes.Numeric)):
        return "float"
    if isinstance(sa_type, sqltypes.DateTime):
        return "datetime"
    return "string"


def _widget(field_type: str, has_relation: bool) -> str:
    if has_relation:
        return "select-relation"
    return {
        "boolean": "checkbox",
        "integer": "number",
        "float": "number",
        "datetime": "datetime",
        "uuid": "uuid-display",
    }.get(field_type, "text")


def _prettify(name: str) -> str:
    return name.replace("_", " ").title()


def _relation_meta(fk, registry) -> RelationMeta:
    """Build RelationMeta from a FK, resolving lookup URL if target is registered."""
    target_table = fk.column.table.name
    lookup_url: str | None = None
    label_field: str | None = None
    if registry is not None:
        target_admin = registry.get(target_table)
        if target_admin is not None:
            lookup_url = f"/api/v1/admin/{target_table}/lookup"
            label_field = (
                target_admin.lookup_field
                or (target_admin.list_display[0] if target_admin.list_display else None)
            )
    return RelationMeta(
        target_table=target_table,
        lookup_url=lookup_url,
        label_field=label_field,
    )


def build_field_metadata(model_admin: ModelAdmin, registry=None) -> list[FieldMeta]:
    """Introspect columns and return FieldMeta list; protected fields are excluded."""
    mapper = sa_inspect(model_admin.model)
    protected = model_admin.all_protected
    readonly_set = set(model_admin.readonly_fields)
    list_set = set(model_admin.list_display)
    search_set = set(model_admin.search_fields)
    filter_set = set(model_admin.filter_fields)

    fields: list[FieldMeta] = []
    for col in mapper.columns:
        name = col.name
        if name in protected:
            continue

        ft = _field_type(col.type)
        has_default = col.default is not None or col.server_default is not None

        relation: RelationMeta | None = None
        if col.foreign_keys:
            fk = next(iter(col.foreign_keys))
            relation = _relation_meta(fk, registry)

        choices_url = getattr(model_admin, "field_choices_urls", {}).get(name)
        fields.append(FieldMeta(
            name=name,
            label=_prettify(name),
            field_type=ft,
            required=not col.nullable and not has_default,
            nullable=bool(col.nullable),
            has_default=has_default,
            readonly=name in readonly_set,
            in_list=name in list_set,
            searchable=name in search_set,
            filterable=name in filter_set,
            sortable=True,
            widget="choices-select" if choices_url else _widget(ft, relation is not None),
            relation=relation,
            choices_url=choices_url,
        ))

    # Virtual create-only fields (e.g. plain-text password before hashing)
    for vname, vtype in model_admin.extra_create_fields.items():
        widget = "password" if "password" in vname else "text"
        fields.append(FieldMeta(
            name=vname,
            label=_prettify(vname),
            field_type="string",
            required=True,
            nullable=False,
            has_default=False,
            readonly=False,
            in_list=False,
            searchable=False,
            filterable=False,
            sortable=False,
            widget=widget,
            create_only=True,
        ))

    return fields


def _build_inline_relations(model_admin: ModelAdmin) -> list[InlineRelationMeta]:
    """Introspect declared relationship attrs listed in model_admin.inline_fields."""
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy.orm import ONETOMANY
    inline: list[InlineRelationMeta] = []
    if not getattr(model_admin, "inline_fields", []):
        return inline
    mapper = sa_inspect(model_admin.model)
    rel_map = {r.key: r for r in mapper.relationships}
    for attr in model_admin.inline_fields:
        rel = rel_map.get(attr)
        if rel is None:
            continue
        target_table = rel.mapper.class_.__tablename__
        many = rel.uselist
        fk_field: str | None = None
        if rel.direction == ONETOMANY and rel.synchronize_pairs:
            # synchronize_pairs: [(parent_col, child_col), ...]
            fk_field = rel.synchronize_pairs[0][1].name
        inline.append(InlineRelationMeta(
            attr=attr,
            target_table=target_table,
            many=many,
            label=attr.replace("_", " ").title(),
            fk_field=fk_field,
        ))
    return inline


def build_model_contract(model_admin: ModelAdmin, registry=None) -> ModelContractMeta:
    fields = build_field_metadata(model_admin, registry=registry)
    from adminfoundry.admin.actions import AdminAction as _AdminAction
    actions = [
        ActionMeta(**(a.to_dict() if isinstance(a, _AdminAction) else a))
        for a in model_admin.actions
    ]
    return ModelContractMeta(
        contract_version=CONTRACT_VERSION,
        model=model_admin.model_name,
        label=model_admin.display_label,
        label_plural=model_admin.display_label_plural,
        description=model_admin.description,
        tenant_scoped=model_admin.tenant_scoped,
        fields=fields,
        list_fields=model_admin.list_display,
        search_fields=model_admin.search_fields,
        filter_fields=model_admin.filter_fields,
        range_filter_fields=getattr(model_admin, "range_filter_fields", []),
        enum_filter_fields=getattr(model_admin, "enum_filter_fields", []),
        ordering=model_admin.ordering,
        readonly_fields=model_admin.readonly_fields,
        actions=actions,
        requires_approval=getattr(model_admin, "requires_approval", False),
        inline_relations=_build_inline_relations(model_admin),
        list_editable=getattr(model_admin, "list_editable", []),
        create_redirect=getattr(model_admin, "create_redirect", "list"),
        permission_matrix=getattr(model_admin, "permission_matrix", False),
    )


def build_model_contract_for_user(
    model_admin: ModelAdmin, user, token_payload: dict, registry=None
) -> ModelContractMeta:
    """Build model contract with policy_visible/policy_editable set for the requesting user."""
    from adminfoundry.authz.policy_engine import policy_engine

    contract = build_model_contract(model_admin, registry=registry)
    updated_fields = [
        f.model_copy(update={
            "policy_visible": policy_engine.evaluate_field(
                user, model_admin, f.name, token_payload
            ).can_view,
            "policy_editable": policy_engine.evaluate_field(
                user, model_admin, f.name, token_payload
            ).can_edit,
        })
        for f in contract.fields
    ]
    return contract.model_copy(update={"fields": updated_fields})
