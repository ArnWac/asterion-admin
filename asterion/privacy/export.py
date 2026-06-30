"""Data-subject export + DSAR log (roadmap G8, DSGVO Art. 15/16/17/18/20).

Two capabilities:

* :func:`export_subject` — assemble a JSON-serialisable bundle of **all the
  personal data asterion holds about one user** across the public/global tables
  (the ``users`` row, tenant memberships, the audit actions they performed,
  impersonation rows they were party to, their saved filters, and their DSAR
  history). This backs the right of access (Art. 15) and data portability
  (Art. 20). Unlike the audit redaction (G7), the subject's own PII is **kept** —
  the point is to return it to them — but *secrets* (``hashed_password``,
  ``totp_secret`` …) are dropped via the :class:`ProtectedFieldRegistry`.

* :func:`record_subject_request` / :func:`list_subject_requests` — the DSAR
  accountability register (Art. 5(2)): who asked for what, when, and the result.

Scope is **public/global only — never a foreign tenant's schema**. Tenant-local
business data is the operator's domain model; dumping it generically would risk
crossing tenant boundaries (cf. the schema-isolation invariant). The bundle says
so explicitly so the right of access stays honest about its boundary.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import or_, select

from asterion.db.session import DatabaseManager
from asterion.models.audit_log import AuditLog
from asterion.models.data_subject_request import DataSubjectRequest
from asterion.models.impersonation_log import ImpersonationLog
from asterion.models.saved_filter import SavedFilter
from asterion.models.tenant_membership import TenantMembership
from asterion.models.user import User
from asterion.security.protected_fields import get_registry

#: GDPR right a DSAR row records.
SubjectRequestType = Literal["access", "export", "rectification", "erasure", "restriction"]
SubjectRequestStatus = Literal["received", "completed", "rejected"]

_REQUEST_TYPES: frozenset[str] = frozenset(
    {"access", "export", "rectification", "erasure", "restriction"}
)
_REQUEST_STATUSES: frozenset[str] = frozenset({"received", "completed", "rejected"})


class SubjectNotFoundError(LookupError):
    """Raised when an export/DSAR targets a user id with no ``users`` row."""


def _jsonable(value: Any) -> Any:
    """Coerce a DB value into a JSON-serialisable form (UUID/datetime/bytes…)."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    if hasattr(value, "isoformat"):  # date / time
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if value.__class__.__module__ == "decimal":
        return str(value)
    return value


def _row_to_dict(row: Any, *, drop_protected: bool = False) -> dict[str, Any]:
    protected = get_registry() if drop_protected else None
    out: dict[str, Any] = {}
    for col in row.__table__.columns:
        if protected is not None and col.name in protected:
            continue
        out[col.name] = _jsonable(getattr(row, col.name))
    return out


async def export_subject(db: DatabaseManager, subject_user_id: uuid.UUID) -> dict[str, Any]:
    """Build the per-person export bundle for ``subject_user_id`` (public scope).

    Raises :class:`SubjectNotFoundError` if no ``users`` row matches.
    """
    async with db.session() as session:
        user = (
            await session.execute(select(User).where(User.id == subject_user_id))
        ).scalar_one_or_none()
        if user is None:
            raise SubjectNotFoundError(f"No user with id {subject_user_id}.")

        memberships = (
            (
                await session.execute(
                    select(TenantMembership).where(TenantMembership.user_id == subject_user_id)
                )
            )
            .scalars()
            .all()
        )
        audit_actions = (
            (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.actor_user_id == subject_user_id)
                    .order_by(AuditLog.created_at)
                )
            )
            .scalars()
            .all()
        )
        impersonations = (
            (
                await session.execute(
                    select(ImpersonationLog)
                    .where(
                        or_(
                            ImpersonationLog.target_user_id == subject_user_id,
                            ImpersonationLog.superadmin_id == subject_user_id,
                        )
                    )
                    .order_by(ImpersonationLog.created_at)
                )
            )
            .scalars()
            .all()
        )
        saved_filters = (
            (
                await session.execute(
                    select(SavedFilter).where(SavedFilter.user_id == str(subject_user_id))
                )
            )
            .scalars()
            .all()
        )
        requests = (
            (
                await session.execute(
                    select(DataSubjectRequest)
                    .where(DataSubjectRequest.subject_user_id == subject_user_id)
                    .order_by(DataSubjectRequest.created_at)
                )
            )
            .scalars()
            .all()
        )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": "public",
        "scope_note": (
            "Public/global data only. Tenant-local business data is the "
            "operator's domain model and is exported per-tenant, not here, to "
            "preserve tenant isolation."
        ),
        "subject": _row_to_dict(user, drop_protected=True),
        "memberships": [_row_to_dict(m) for m in memberships],
        "audit_actions": [_row_to_dict(a) for a in audit_actions],
        "impersonations": [_row_to_dict(i) for i in impersonations],
        "saved_filters": [_row_to_dict(f) for f in saved_filters],
        "data_subject_requests": [_row_to_dict(r) for r in requests],
    }


async def record_subject_request(
    db: DatabaseManager,
    *,
    subject_user_id: uuid.UUID,
    request_type: SubjectRequestType,
    status: SubjectRequestStatus = "received",
    handled_by_user_id: uuid.UUID | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Append a DSAR row and return it as a dict.

    ``ValueError`` for an unknown ``request_type`` / ``status``.
    """
    if request_type not in _REQUEST_TYPES:
        raise ValueError(
            f"Unknown request_type {request_type!r}; expected one of {_REQUEST_TYPES}."
        )
    if status not in _REQUEST_STATUSES:
        raise ValueError(f"Unknown status {status!r}; expected one of {_REQUEST_STATUSES}.")

    async with db.session() as session:
        async with session.begin():
            row = DataSubjectRequest(
                subject_user_id=subject_user_id,
                request_type=request_type,
                status=status,
                handled_by_user_id=handled_by_user_id,
                note=note,
            )
            session.add(row)
        await session.refresh(row)
        return _row_to_dict(row)


async def list_subject_requests(
    db: DatabaseManager, subject_user_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Return the DSAR log for ``subject_user_id``, oldest first."""
    async with db.session() as session:
        rows = (
            (
                await session.execute(
                    select(DataSubjectRequest)
                    .where(DataSubjectRequest.subject_user_id == subject_user_id)
                    .order_by(DataSubjectRequest.created_at)
                )
            )
            .scalars()
            .all()
        )
    return [_row_to_dict(r) for r in rows]
