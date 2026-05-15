"""Dashboard widget base types."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DashboardWidgetContext:
    user: Any
    db: Any
    request: Any
    tenant: Any | None = None
    is_superadmin: bool = False
    capabilities: frozenset[str] = field(default_factory=frozenset)


class DashboardWidget:
    """Base class for dashboard widgets."""

    id: str = ""
    title: str = ""
    superadmin_only: bool = False
    required_capabilities: frozenset[str] = frozenset()

    def widget_type(self) -> str:
        return "stats"

    async def get_data(self, user: Any, db: Any, request: Any) -> dict:
        return {}
