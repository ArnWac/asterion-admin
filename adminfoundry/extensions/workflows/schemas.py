import uuid
from datetime import datetime
from pydantic import BaseModel, field_validator


class ChangeRequestCreate(BaseModel):
    model_name: str
    object_id: str | None = None
    operation: str  # create | update | delete
    proposed_data: dict | None = None
    reason: str | None = None

    @field_validator("operation")
    @classmethod
    def validate_operation(cls, v: str) -> str:
        if v not in ("create", "update", "delete"):
            raise ValueError("operation must be 'create', 'update', or 'delete'")
        return v


class ReviewRequest(BaseModel):
    action: str  # approve | reject
    reason: str | None = None

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        if v not in ("approve", "reject"):
            raise ValueError("action must be 'approve' or 'reject'")
        return v


class RevertRequest(BaseModel):
    reason: str


class ChangeRequestRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    model_name: str
    object_id: str | None
    operation: str
    requester_id: uuid.UUID
    reviewer_id: uuid.UUID | None
    status: str
    reason: str | None
    rejection_reason: str | None
    tenant_id: uuid.UUID | None
    audit_log_id: uuid.UUID | None
