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
    # URL to fetch <select> choices from (response: {models:[]} or {items:[]})
    choices_url: str | None = None


class ActionMeta(BaseModel):
    name: str
    label: str
    danger: bool = False
    confirm: bool = False
    bulk: bool = False
    single: bool = True
    # Phase 11: async execution hint for clients
    async_execution: bool = False


class InlineRelationMeta(BaseModel):
    """Describes a relationship that can be edited inline in the detail/edit view."""
    attr: str          # Python relationship attribute name on the model
    target_table: str  # __tablename__ of the related model
    many: bool         # True for M2M / one-to-many; False for FK (single)
    label: str
    fk_field: str | None = None  # FK column on child that points to parent (one-to-many only)


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
    range_filter_fields: list[str] = []
    enum_filter_fields: list[str] = []
    ordering: list[str]
    readonly_fields: list[str]
    actions: list[ActionMeta]
    # Phase 13: workflow capability flag
    requires_approval: bool = False
    # Inline-editable relationships
    inline_relations: list[InlineRelationMeta] = []
    # List-editable field names
    list_editable: list[str] = []
    # Redirect target after create: "list" | "detail"
    create_redirect: str = "list"
    # True: render permission matrix section (role-like models)
    permission_matrix: bool = False
    # False: deletion blocked at API level (e.g. immutable audit logs)
    allow_delete: bool = True
