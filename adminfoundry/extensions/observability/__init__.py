"""Observability extension — in-process admin metrics.

Provides: request/action/audit counters, contract version tracking, client type tracking.
Replace the in-process counters with Prometheus or OpenTelemetry in production.

Optional: add ObservabilityExtension() to CoreAdminConfig.extensions to enable.
Counters are collected by middleware automatically once the extension is registered.
"""
from adminfoundry.extensions import ExtensionBase


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

    def startup_check(self) -> None:
        from adminfoundry.extensions.observability.admin_metrics import get_snapshot  # noqa: F401


__all__ = ["ObservabilityExtension"]
