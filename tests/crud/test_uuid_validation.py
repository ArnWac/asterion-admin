"""Bad UUID input is a 422, not a 500.

The write path filters field names but not column *types*, so a non-UUID value
for a GUID column (e.g. a free-text ``project_id`` of "test") used to reach the
driver and surface as a 500 from ``GUID.process_bind_param``.
``validate_uuid_fields`` catches it as a field error first.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from asterion.crud.payload import validate_uuid_fields
from asterion.models.impersonation_log import ImpersonationLog


def test_rejects_non_uuid_value():
    with pytest.raises(HTTPException) as exc:
        validate_uuid_fields({"superadmin_id": "test"}, ImpersonationLog)
    assert exc.value.status_code == 422
    assert "superadmin_id" in exc.value.detail["fields"]


def test_accepts_valid_uuid_string_and_object():
    # Both a canonical string and a real UUID pass; None is ignored.
    validate_uuid_fields({"superadmin_id": str(uuid.uuid4())}, ImpersonationLog)
    validate_uuid_fields({"target_user_id": uuid.uuid4()}, ImpersonationLog)
    validate_uuid_fields({"tenant_id": None}, ImpersonationLog)


def test_ignores_non_uuid_columns():
    # jti is a String column — never UUID-validated.
    validate_uuid_fields({"jti": "not-a-uuid-and-thats-fine"}, ImpersonationLog)
