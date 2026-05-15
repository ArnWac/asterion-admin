"""Observability extension — admin metrics dashboard widgets, Prometheus exporter,
and the in-process runtime counter store.

All metric state lives inside this extension namespace. Core infrastructure
must not import from here — counters are written by middleware stubs and read
only when ObservabilityExtension is registered.

Optional: add ObservabilityExtension() to CoreAdminConfig.extensions to enable
the metrics dashboard widget and /metrics + /api/v1/admin/metrics routes.
"""
from adminfoundry.extensions import ExtensionBase
from adminfoundry.extensions.observability.widgets import AdminMetricsWidget


class ObservabilityExtension(ExtensionBase):
    name = "observability"
    version = "0.1.0"
    is_optional = True

    def get_capabilities(self) -> dict:
        return {
            "request_counters": True,
            "action_counters": True,
            "audit_failure_tracking": True,
            "contract_version_usage": True,
            "client_type_tracking": True,
        }

    def get_routers(self) -> list:
        from adminfoundry.extensions.observability.router import prometheus_router, admin_metrics_router
        return [prometheus_router, admin_metrics_router]

    def get_dashboard_widgets(self) -> list:
        return [AdminMetricsWidget()]

    def startup_check(self) -> None:
        from adminfoundry.extensions.observability.admin_metrics import get_snapshot  # noqa: F401


__all__ = ["ObservabilityExtension", "AdminMetricsWidget"]
