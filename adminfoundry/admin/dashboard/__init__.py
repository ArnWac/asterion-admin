from adminfoundry.admin.dashboard.widget import DashboardWidget
from adminfoundry.admin.dashboard.builtins import ModelCountsWidget, DEFAULT_WIDGETS
from adminfoundry.admin.dashboard.registry import DashboardRegistry, dashboard_registry

__all__ = [
    "DashboardWidget",
    "ModelCountsWidget",
    "DEFAULT_WIDGETS",
    "DashboardRegistry",
    "dashboard_registry",
]
