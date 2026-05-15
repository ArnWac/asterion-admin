"""Core built-in dashboard widgets."""
from __future__ import annotations
from typing import Any

from adminfoundry.admin.dashboard.widget import DashboardWidget


class ModelCountsWidget(DashboardWidget):
    """Shows record counts per registered model, tenant-scoped where applicable."""

    id = "model_counts"
    title = "Records"

    def widget_type(self) -> str:
        return "counts"

    async def get_data(self, user: Any, db: Any, request: Any) -> dict:
        from sqlalchemy import select, func as sa_func
        from adminfoundry.admin.registry import admin_site

        tenant = getattr(request.state, "tenant", None)
        rows = []
        for ma in admin_site.all():
            if tenant is not None and not ma.tenant_scoped:
                continue
            if tenant is None and ma.tenant_scoped and not getattr(ma, "global_only_in_root_panel", False):
                continue
            stmt = select(sa_func.count()).select_from(ma.model)
            if ma.tenant_scoped and hasattr(ma.model, "tenant_id"):
                if tenant:
                    stmt = stmt.where(ma.model.tenant_id == tenant.id)
                elif getattr(ma, "global_only_in_root_panel", False):
                    stmt = stmt.where(ma.model.tenant_id.is_(None))
            count = await db.scalar(stmt) or 0
            rows.append({"model": ma.model_name, "label": ma.label_plural, "count": count})
        return {"rows": rows}


DEFAULT_WIDGETS: list[DashboardWidget] = [ModelCountsWidget()]
