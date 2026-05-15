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
# Canonical location is adminfoundry.admin.dashboard — this module is a compat re-export.
from adminfoundry.admin.dashboard.widget import DashboardWidget
from adminfoundry.admin.dashboard.builtins import ModelCountsWidget, DEFAULT_WIDGETS

__all__ = ["DashboardWidget", "ModelCountsWidget", "DEFAULT_WIDGETS"]
