"""Per-user navigation endpoint — Phase 9.

Returns the subset of :class:`asterion.ui.navigation.NavigationItem`
the calling principal can actually use, given their permission set.

Why a separate endpoint (not embedded in ``/_contract``):
``/_contract`` is identical for every authenticated user — that's a
documented invariant the UI caches against. Navigation is per-user,
so it gets its own endpoint that the UI fetches once per page load.

Why filter server-side (not in JS):
the principal's permission set is never shipped to the browser, so the
client genuinely cannot decide what to hide. Filtering here also keeps
the navigation "what could I click on" answer authoritative — the same
source of truth that route-level permission checks consult.

Superadmin bypass: superadmins (``ctx.is_superadmin``) see every
registered item regardless of permission key. The built-in permission
provider only grants ``admin.*`` to superadmins, which wouldn't match
extension-owned namespaces like ``oauth.identities.list`` without this
explicit short-circuit.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from asterion.admin.context import AdminContext, require_admin_context

router = APIRouter()


@router.get("/_navigation")
async def get_navigation(
    request: Request,
    ctx: AdminContext = Depends(require_admin_context),
) -> dict:
    runtime = request.app.state.asterion
    items = runtime.navigation.all()

    visible = [
        {"id": item.id, "label": item.label, "path": item.path}
        for item in items
        if ctx.is_superadmin or ctx.has_permission(item.permission)
    ]
    return {"items": visible}
