"""Request-scoped admin context — the single object every router will read
from once Phase 2 lands.

Phase 1 ships :class:`AdminContext` and the :func:`build_admin_context`
dependency, but no router consumes them yet. They sit alongside the
existing ``get_current_user`` / ``require_tenant_auth_context`` paths and
are exercised only by the provider tests. Phase 2 wires them in.

The context is built once per request from the four providers stored on
``runtime``. It carries:

* the resolved :class:`~adminfoundry.providers.base.AdminUser` (None for
  anonymous),
* the resolved :class:`~adminfoundry.providers.base.AdminTenant` (None
  for public-scope requests),
* the user's permission keys for that tenant,
* a ``source`` label (``"ui"`` / ``"api"`` / ``"import"`` / ``"job"``)
  so downstream consumers (audit, hooks) can branch on origin without
  re-parsing the request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from fastapi import Request

from adminfoundry.providers.base import AdminTenant, AdminUser

Source = Literal["ui", "api", "import", "job"]


@dataclass(slots=True)
class AdminContext:
    """Neutral per-request context.

    Pass-through value object — no methods that mutate state, no hidden
    framework references. Built by :func:`build_admin_context`.
    """

    request: Request | None
    user: AdminUser | None
    tenant: AdminTenant | None
    permissions: frozenset[str] = field(default_factory=frozenset)
    source: Source = "api"
    action: str | None = None

    @property
    def is_authenticated(self) -> bool:
        return self.user is not None

    @property
    def is_superadmin(self) -> bool:
        return bool(self.user and self.user.is_superadmin)

    def has_permission(self, key: str) -> bool:
        """Cheap convenience around the existing wildcard matcher."""
        from adminfoundry.authz.permissions import has_permission

        return has_permission(self.permissions, key)


def _detect_source(request: Request) -> Source:
    """Heuristic source label.

    UI requests land on ``/admin/<path>`` (no ``/api/`` prefix). Anything
    else from the HTTP layer counts as ``"api"``. CLI / background-job
    callers build the context manually with ``source="import"`` /
    ``"job"`` and don't go through this function.
    """
    path = request.url.path
    ui_path = request.app.state.adminfoundry.config.admin_ui_path
    if ui_path and path.startswith(ui_path) and not path.startswith(
        request.app.state.adminfoundry.config.admin_api_prefix
    ):
        return "ui"
    return "api"


async def build_admin_context(request: Request) -> AdminContext:
    """FastAPI dependency that assembles the per-request :class:`AdminContext`.

    Anonymous requests resolve to a context with ``user=None``. The
    framework does not raise 401 here — that's a route-level decision,
    because some endpoints (login, health) are intentionally public.
    """
    runtime = request.app.state.adminfoundry
    providers = runtime.providers

    identity = await providers.auth.authenticate_request(request)
    user: AdminUser | None = None
    if identity is not None:
        user = await providers.users.get_by_id(identity.user_id, request=request)

    tenant = await providers.tenants.resolve_tenant(request)

    permissions: frozenset[str] = frozenset()
    if user is not None:
        permissions = await providers.permissions.get_permissions(
            user, tenant, request=request
        )

    return AdminContext(
        request=request,
        user=user,
        tenant=tenant,
        permissions=permissions,
        source=_detect_source(request),
    )
