from pydantic import BaseModel


class FieldPolicyMeta(BaseModel):
    field: str
    can_view: bool
    can_edit: bool


class ModelPolicyResponse(BaseModel):
    model: str
    contract_version: str
    can_list: bool
    can_create: bool
    can_read: bool
    can_update: bool
    can_delete: bool
    field_policies: list[FieldPolicyMeta]
