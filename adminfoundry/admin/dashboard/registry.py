"""Dashboard widget registry — collects core, extension, and app widgets."""
from __future__ import annotations
from typing import Any

from adminfoundry.admin.dashboard.widget import DashboardWidget
from adminfoundry.admin.dashboard.builtins import DEFAULT_WIDGETS


class DashboardRegistry:
    def __init__(self) -> None:
        self._widgets: list[DashboardWidget] = []

    def reset(self, base: list[DashboardWidget] | None = None) -> None:
        """Re-initialize with core defaults or a user-supplied base list."""
        self._widgets = list(base if base is not None else DEFAULT_WIDGETS)

    def register(self, widget: DashboardWidget) -> None:
        self._widgets.append(widget)

    def register_from_extension(self, ext: Any) -> None:
        if hasattr(ext, "get_dashboard_widgets"):
            self._widgets.extend(ext.get_dashboard_widgets())

    def all(self) -> list[DashboardWidget]:
        return list(self._widgets)

    def for_user(self, is_superadmin: bool) -> list[DashboardWidget]:
        return [w for w in self._widgets if not w.superadmin_only or is_superadmin]


dashboard_registry = DashboardRegistry()
