"""
Simple in-process admin metrics counters.
Never exposes secrets, token internals, or protected field content.
Replace with Prometheus/OpenTelemetry counters in production.
"""
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class _Counters:
    request_count: int = 0
    request_errors: int = 0
    action_count: int = 0
    action_errors: int = 0
    audit_write_failures: int = 0
    contract_version_usage: dict = field(default_factory=lambda: defaultdict(int))
    # "builtin_ui" | "external" | "unknown"
    client_type_counts: dict = field(default_factory=lambda: defaultdict(int))


_c = _Counters()


def increment_requests(error: bool = False) -> None:
    _c.request_count += 1
    if error:
        _c.request_errors += 1


def increment_actions(error: bool = False) -> None:
    _c.action_count += 1
    if error:
        _c.action_errors += 1


def record_audit_failure() -> None:
    _c.audit_write_failures += 1


def record_contract_version(version: str) -> None:
    _c.contract_version_usage[version] += 1


def record_client_type(client_type: str) -> None:
    _c.client_type_counts[client_type] += 1


def get_snapshot() -> dict:
    return {
        "request_count": _c.request_count,
        "request_errors": _c.request_errors,
        "action_count": _c.action_count,
        "action_errors": _c.action_errors,
        "audit_write_failures": _c.audit_write_failures,
        "contract_version_usage": dict(_c.contract_version_usage),
        "client_type_counts": dict(_c.client_type_counts),
    }


def reset() -> None:
    global _c
    _c = _Counters()
