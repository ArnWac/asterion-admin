"""Dashboard widgets contributed by ObservabilityExtension.

Reads from the observability counter store at
`adminfoundry.extensions.observability.admin_metrics`. Never exposes secrets,
token internals, or protected field content.
"""
from typing import Any

from adminfoundry.admin.dashboard.widget import DashboardWidget


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


__all__ = ["AdminMetricsWidget"]
