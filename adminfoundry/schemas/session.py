import uuid
from datetime import datetime
from pydantic import BaseModel


class SessionRead(BaseModel):
    jti: str
    user_id: uuid.UUID
    created_at: datetime
    expires_at: datetime
    ip_address: str | None
    user_agent: str | None
    is_active: bool


class SessionRevoke(BaseModel):
    jti: str
