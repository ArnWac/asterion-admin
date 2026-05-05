import uuid
from datetime import datetime
from pydantic import BaseModel


class JobRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    status: str
    job_type: str
    model_name: str | None
    action_name: str | None
    initiator_id: uuid.UUID
    tenant_id: uuid.UUID | None
    progress: int | None
    result_summary: str | None
    failure_summary: str | None


class BulkActionRequest(BaseModel):
    action: str
    object_ids: list[uuid.UUID]
    confirm: bool = False
    idempotency_key: str | None = None


class ImportRowResult(BaseModel):
    row_index: int
    success: bool
    errors: list[str] = []
    data: dict | None = None


class ImportRequest(BaseModel):
    rows: list[dict]
    dry_run: bool = True
    idempotency_key: str | None = None


class ImportResult(BaseModel):
    dry_run: bool
    total: int
    success_count: int
    error_count: int
    rows: list[ImportRowResult]
    job_id: uuid.UUID | None = None


class ExportResult(BaseModel):
    job_id: uuid.UUID
    status: str
    row_count: int | None
    data: list[dict] | None = None
