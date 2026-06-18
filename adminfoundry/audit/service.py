"""Audit log writer.

Two integration points:

  * :func:`record_audit_in_session` — preferred from request handlers. Adds
    the audit row to the request-scoped session inside a SAVEPOINT so a
    failed audit insert is rolled back without poisoning the main
    transaction.
  * :func:`record_audit` — for contexts that don't already own a session
    (CLI tools, lifecycle hooks). Opens its own session.

Both helpers catch and log every internal failure: an audit miss must never
surface as a 500.

Audit values that may contain secrets (request bodies, change diffs) are
sanitized through :func:`adminfoundry.security.sanitize.sanitize_payload`
before they touch the database.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from adminfoundry.core.net import request_client_ip
from adminfoundry.db.session import DatabaseManager
from adminfoundry.models.audit_log import AuditLog
from adminfoundry.providers.base import AdminPrincipal
from adminfoundry.security.sanitize import sanitize_payload

logger = logging.getLogger(__name__)


LOGIN_SUCCESS = "login_success"
LOGIN_FAILURE = "login_failure"
LOGOUT = "logout"
LOGOUT_ALL = "logout_all"
PASSWORD_RESET_REQUEST = "password_reset_request"
PASSWORD_RESET_CONFIRM = "password_reset_confirm"
TWO_FACTOR_ENABLED = "two_factor_enabled"
TWO_FACTOR_DISABLED = "two_factor_disabled"
CRUD_CREATE = "crud_create"
CRUD_UPDATE = "crud_update"
CRUD_DELETE = "crud_delete"
ADMIN_ACTION = "admin_action"
IMPERSONATION_START = "impersonation_start"
IMPERSONATION_STOP = "impersonation_stop"


def request_audit_kwargs(
    request: Request | None,
    *,
    status_code: int,
) -> dict[str, Any]:
    """Extract method/path/status_code/ip_address from a Request."""
    if request is None:
        return {"method": "", "path": "", "status_code": status_code, "ip_address": None}
    return {
        "method": request.method,
        "path": request.url.path,
        "status_code": status_code,
        "ip_address": request_client_ip(request),
    }


def _coerce_actor_id(actor: AdminPrincipal | None) -> uuid.UUID | None:
    """Convert the principal's id to a UUID for ``actor_user_id``.

    The builtin User model uses UUID primary keys, but external auth
    providers may supply opaque string ids (Keycloak sub, OAuth subject).
    UUID-shaped ids round-trip into the audit column; everything else
    falls back to ``None`` and survives in ``actor_label`` instead — the
    audit row still pins WHO did the action, just via the email/display
    name instead of the FK column.
    """
    if actor is None:
        return None
    raw = getattr(actor, "id", None)
    if raw is None:
        return None
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError):
        return None


def audit_payload(
    *,
    action: str,
    method: str = "",
    path: str = "",
    status_code: int = 0,
    actor: AdminPrincipal | None = None,
    tenant_id: uuid.UUID | None = None,
    resource: str | None = None,
    record_id: str | int | uuid.UUID | None = None,
    changes: Mapping[str, Any] | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    """Build an AuditLog row, sanitizing ``changes`` along the way.

    Performs no I/O — callers persist the row.
    """
    sanitized: dict[str, Any] | None = None
    if changes is not None:
        sanitized = sanitize_payload(dict(changes))

    return AuditLog(
        method=method,
        path=path,
        status_code=status_code,
        actor_user_id=_coerce_actor_id(actor),
        tenant_id=tenant_id,
        resource=resource,
        record_id=None if record_id is None else str(record_id),
        action=action,
        actor_label=getattr(actor, "email", None),
        changes=sanitized,
        ip_address=ip_address,
    )


async def record_audit_in_session(session: AsyncSession, **kw: Any) -> None:
    """Add an audit row to ``session`` inside a SAVEPOINT.

    The savepoint isolates the audit insert from the main transaction so a
    failure here cannot roll back the caller's work. Any error is logged
    and swallowed — this function never raises.
    """
    try:
        async with session.begin_nested():
            session.add(audit_payload(**kw))
            await session.flush()
    except Exception:
        logger.warning(
            "audit write failed (in session) for action=%s",
            kw.get("action"),
            exc_info=True,
        )


async def record_audit(db: DatabaseManager, **kw: Any) -> None:
    """Append using an isolated session. Never raises."""
    try:
        async with db.session() as session:
            async with session.begin():
                session.add(audit_payload(**kw))
    except Exception:
        logger.warning(
            "audit write failed for action=%s",
            kw.get("action"),
            exc_info=True,
        )
