"""
Audit middleware: writes a best-effort AuditLog after every response.
Failures are silently swallowed — must never affect the main flow.
"""
import uuid
from starlette.middleware.base import BaseHTTPMiddleware


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        try:
            from adminfoundry.database import AsyncSessionLocal
            from adminfoundry.models.audit_log import AuditLog

            user_id_str = getattr(request.state, "audit_user_id", None)
            tenant = getattr(request.state, "tenant", None)

            log = AuditLog(
                method=request.method,
                path=str(request.url.path),
                status_code=response.status_code,
                user_id=uuid.UUID(user_id_str) if user_id_str else None,
                tenant_id=tenant.id if tenant else None,
                action=getattr(request.state, "audit_action", None),
                object_id=getattr(request.state, "audit_object_id", None),
                actor=getattr(request.state, "audit_actor", None),
                changes=getattr(request.state, "audit_changes", None),
            )
            async with AsyncSessionLocal() as session:
                session.add(log)
                await session.commit()
        except Exception:
            pass  # audit failure must never affect the response
        return response
