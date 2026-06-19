"""Request-scoped admin context — the single neutral object every router consumes.

Built once per request from the four providers on
:class:`asterion.core.runtime.AdminRuntime`. It carries:

* the resolved :class:`~asterion.providers.base.AdminPrincipal`
  (None for anonymous),
* the resolved :class:`~asterion.providers.base.AdminTenant`
  (None for public-scope requests),
* the principal's permission keys for that tenant,
* the principal's role names (empty unless the PermissionProvider also
  surfaces roles — kept as a separate field so policies that branch on
  role name don't have to scan permission strings),
* a ``source`` label (``"ui"`` / ``"api"`` / ``"import"`` / ``"job"``)
  so downstream consumers (audit, hooks) can branch on origin without
  re-parsing the request.

Routes obtain it via FastAPI dependency injection — either
:func:`build_admin_context` (anonymous-tolerant) or
:func:`require_admin_context` (raises 401 on missing principal).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from fastapi import HTTPException, Request, status

from asterion.providers.base import AdminPrincipal, AdminTenant

Source = Literal["ui", "api", "import", "job"]


@dataclass(slots=True)
class AdminContext:
    """Neutral per-request context.

    Pass-through value object — no methods that mutate state, no hidden
    framework references. Built by :func:`build_admin_context`.
    """

    request: Request | None
    principal: AdminPrincipal | None
    tenant: AdminTenant | None
    permissions: frozenset[str] = field(default_factory=frozenset)
    roles: frozenset[str] = field(default_factory=frozenset)
    source: Source = "api"
    action: str | None = None

    @property
    def is_authenticated(self) -> bool:
        return self.principal is not None

    @property
    def is_superadmin(self) -> bool:
        return bool(self.principal and self.principal.is_superadmin)

    def has_permission(self, key: str) -> bool:
        """Cheap convenience around the existing wildcard matcher."""
        from asterion.authz.permissions import has_permission

        return has_permission(self.permissions, key)


def _detect_source(request: Request) -> Source:
    """Heuristic source label.

    UI requests land on ``/admin/<path>`` (no ``/api/`` prefix). Anything
    else from the HTTP layer counts as ``"api"``. CLI / background-job
    callers build the context manually with ``source="import"`` /
    ``"job"`` and don't go through this function.
    """
    path = request.url.path
    ui_path = request.app.state.asterion.config.admin_ui_path
    if (
        ui_path
        and path.startswith(ui_path)
        and not path.startswith(request.app.state.asterion.config.admin_api_prefix)
    ):
        return "ui"
    return "api"


async def build_admin_context(request: Request) -> AdminContext:
    """FastAPI dependency that assembles the per-request :class:`AdminContext`.

    Anonymous requests resolve to a context with ``principal=None``. The
    framework does not raise 401 here — that's a route-level decision,
    because some endpoints (login, health) are intentionally public.
    """
    runtime = request.app.state.asterion
    providers = runtime.providers

    identity = await providers.auth.authenticate_request(request)
    principal: AdminPrincipal | None = None
    if identity is not None:
        principal = await providers.users.get_by_id(identity.user_id, request=request)

    tenant = await providers.tenants.resolve_tenant(request)

    permissions: frozenset[str] = frozenset()
    if principal is not None:
        permissions = await providers.permissions.get_permissions(
            principal, tenant, request=request
        )

    ctx = AdminContext(
        request=request,
        principal=principal,
        tenant=tenant,
        permissions=permissions,
        source=_detect_source(request),
    )

    # AccessLogMiddleware (core/middleware.py) reads ``request.state.current_user``
    # for the per-request ``actor_user_id`` log field. Keep that working without
    # forcing routes to depend on the legacy ``get_current_user`` dependency.
    if principal is not None:
        request.state.current_user = principal
    return ctx


async def require_admin_context(request: Request) -> AdminContext:
    """Authenticated variant of :func:`build_admin_context`.

    Returns the context if a principal is present; raises 401 otherwise.
    Use this on routes that require an authenticated caller — every CRUD,
    contract, action, and import/export route in v1 does.
    """
    ctx = await build_admin_context(request)
    if ctx.principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return ctx
