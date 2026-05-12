"""
Pluggable dashboard widget system.

Register custom widgets via CoreAdminConfig::

    from adminfoundry.dashboard import DashboardWidget

    class RevenueWidget(DashboardWidget):
        id = "revenue"
        title = "Revenue"

        async def get_data(self, user, db, request):
            total = await db.scalar(select(func.sum(Order.amount)))
            return {"stats": [{"label": "Total revenue", "value": f"€{total or 0:,.2f}"}]}

    config = CoreAdminConfig(dashboard_widgets=[RevenueWidget()])

Widget types
------------
- ``stats``  — list of {label, value, sub?} cards
- ``counts`` — model record counts table (built-in, generated automatically)
"""
from __future__ import annotations
from typing import Any


class DashboardWidget:
    """Base class for dashboard widgets."""

    id: str = ""
    title: str = ""
    superadmin_only: bool = False

    def widget_type(self) -> str:
        return "stats"

    async def get_data(self, user: Any, db: Any, request: Any) -> dict:
        return {}


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
            # Match navigation visibility rules
            if tenant is not None and not ma.tenant_scoped:
                continue  # hide global models in tenant dashboard
            if tenant is None and ma.tenant_scoped and not getattr(ma, "global_only_in_root_panel", False):
                continue  # hide pure tenant models in root dashboard
            stmt = select(sa_func.count()).select_from(ma.model)
            if ma.tenant_scoped and hasattr(ma.model, "tenant_id"):
                if tenant:
                    stmt = stmt.where(ma.model.tenant_id == tenant.id)
                elif getattr(ma, "global_only_in_root_panel", False):
                    stmt = stmt.where(ma.model.tenant_id.is_(None))
            count = await db.scalar(stmt) or 0
            rows.append({"model": ma.model_name, "label": ma.label_plural, "count": count})
        return {"rows": rows}


class AdminMetricsWidget(DashboardWidget):
    """Global request/action counters — superadmin only."""

    id = "admin_metrics"
    title = "Operations"
    superadmin_only = True

    async def get_data(self, user: Any, db: Any, request: Any) -> dict:
        from adminfoundry.extensions.observability.admin_metrics import get_snapshot
        m = get_snapshot()
        error_rate = (
            f"{round(m['request_errors'] / m['request_count'] * 100)}%" if m["request_count"] else "—"
        )
        stats = [
            {"label": "Requests", "value": m["request_count"],
             "sub": f"{m['request_errors']} errors · {error_rate} error rate"},
            {"label": "Actions", "value": m["action_count"],
             "sub": f"{m['action_errors']} errors" if m["action_errors"] else "no errors"},
            {"label": "Audit failures", "value": m["audit_write_failures"]},
        ]
        client_stats = [
            {"label": k, "value": v} for k, v in (m.get("client_type_counts") or {}).items()
        ]
        return {"stats": stats, "client_stats": client_stats}


DEFAULT_WIDGETS: list[DashboardWidget] = [ModelCountsWidget(), AdminMetricsWidget()]
