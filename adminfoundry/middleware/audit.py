"""
Audit middleware: schedules a best-effort AuditLog write as a BackgroundTask
so the response is sent to the client before the DB write occurs.
Failures are silently swallowed — must never affect the main flow.
"""
import uuid
from starlette.background import BackgroundTask, BackgroundTasks
from starlette.middleware.base import BaseHTTPMiddleware


async def _write_audit(log_data: dict) -> None:
    try:
        from adminfoundry.database import AsyncSessionLocal
        from adminfoundry.models.audit_log import AuditLog
        async with AsyncSessionLocal() as session:
            session.add(AuditLog(**log_data))
            await session.commit()
    except Exception:
        pass


_MUTABLE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        try:
            # Only log mutating requests, or GET requests the handler explicitly tagged.
            is_mutable = request.method in _MUTABLE_METHODS
            has_explicit_action = bool(getattr(request.state, "audit_action", None))
            if not is_mutable and not has_explicit_action:
                return response

            user_id_str = getattr(request.state, "audit_user_id", None)
            tenant = getattr(request.state, "tenant", None)
            log_data = {
                "method": request.method,
                "path": str(request.url.path),
                "status_code": response.status_code,
                "user_id": uuid.UUID(user_id_str) if user_id_str else None,
                "tenant_id": tenant.id if tenant else None,
                "action": getattr(request.state, "audit_action", None),
                "object_id": getattr(request.state, "audit_object_id", None),
                "actor": getattr(request.state, "audit_actor", None),
                "changes": getattr(request.state, "audit_changes", None),
            }
            audit_task = BackgroundTask(_write_audit, log_data)
            if response.background is None:
                response.background = audit_task
            else:
                combined = BackgroundTasks()
                combined.tasks.append(response.background)
                combined.tasks.append(audit_task)
                response.background = combined
        except Exception:
            pass
        return response
