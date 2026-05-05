from __future__ import annotations
from pydantic import BaseModel


class RelationMeta(BaseModel):
    """Minimal relation metadata for FK fields — no backend internals."""
    target_table: str
    lookup_url: str | None = None
    label_field: str | None = None


class RendererHints(BaseModel):
    """Phase 14: optional advanced renderer hints for enterprise clients."""
    advanced_widget: str | None = None
    column_width: str | None = None
    inline_editable: bool = False
    truncate_list: bool = False


class FieldMeta(BaseModel):
    name: str
    label: str
    field_type: str
    required: bool
    nullable: bool
    has_default: bool
    readonly: bool
    in_list: bool
    searchable: bool
    filterable: bool
    sortable: bool
    widget: str
    relation: RelationMeta | None = None
    policy_visible: bool = True
    policy_editable: bool = True
    create_only: bool = False
    # Phase 14: optional advanced renderer hints
    renderer_hints: RendererHints | None = None


class ActionMeta(BaseModel):
    name: str
    label: str
    danger: bool = False
    confirm: bool = False
    bulk: bool = False
    single: bool = True
    # Phase 11: async execution hint for clients
    async_execution: bool = False


class ModelContractMeta(BaseModel):
    contract_version: str
    model: str
    label: str
    label_plural: str
    description: str | None
    tenant_scoped: bool
    fields: list[FieldMeta]
    list_fields: list[str]
    search_fields: list[str]
    filter_fields: list[str]
    ordering: list[str]
    readonly_fields: list[str]
    actions: list[ActionMeta]
    # Phase 13: workflow capability flag
    requires_approval: bool = False
