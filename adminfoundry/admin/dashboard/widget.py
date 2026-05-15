"""Dashboard widget base types."""
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
