"""G20 — optional observability (OpenTelemetry span + Prometheus metrics).

The active path is exercised when ``prometheus-client`` / ``opentelemetry-api``
are installed (the ``[observability]`` extra). The graceful no-op path (backend
absent) is covered by constructing an inert ``Observability`` directly, so the
test doesn't depend on uninstalling anything.
"""

from __future__ import annotations

import importlib.util
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from asterion import CoreAdminConfig, create_admin
from asterion.core.observability import (
    Observability,
    build_metrics_router,
    build_observability,
)

_HAS_PROM = importlib.util.find_spec("prometheus_client") is not None
_HAS_OTEL = importlib.util.find_spec("opentelemetry") is not None
_needs_prom = pytest.mark.skipif(not _HAS_PROM, reason="needs prometheus-client ([observability])")


# --- no-op behaviour (backend-absent path), no deps required ---


def test_inert_observability_is_all_noop():
    obs = Observability()  # no registry, no tracer
    assert obs.metrics_available is False
    assert obs.tracing_available is False
    assert obs.metrics_exposition() is None
    # Must not raise:
    obs.record_request(method="GET", route="/x", status_code=200, duration_s=0.1)
    with obs.span("http.request", {"a": 1}) as span:
        assert span is None
    obs.set_span_attributes(None, {"k": "v"})  # no-op


def test_build_observability_disabled_returns_none():
    assert build_observability(enabled=False, service_name="svc") is None


def test_metrics_endpoint_503_when_backend_absent():
    """When observability is on but Prometheus isn't installed, /metrics returns
    a clear 503 rather than a 404."""
    app = FastAPI()
    app.state.asterion = SimpleNamespace(observability=Observability(registry=None))
    app.include_router(build_metrics_router("/metrics"))
    resp = TestClient(app).get("/metrics")
    assert resp.status_code == 503
    assert "not installed" in resp.text


# --- active path (requires the [observability] extra) ---


def _obs_app(tmp_path):
    return create_admin(
        config=CoreAdminConfig(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'obs.db'}",
            secret_key="test-obs-secret",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
            observability_enabled=True,
        )
    )


@_needs_prom
def test_disabled_by_default_no_metrics_route(tmp_path):
    app = create_admin(
        config=CoreAdminConfig(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'noobs.db'}",
            secret_key="test-obs-secret",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        )
    )
    assert app.state.asterion.observability is None
    with TestClient(app, raise_server_exceptions=False) as c:
        assert c.get("/metrics").status_code == 404


@_needs_prom
def test_metrics_endpoint_exposes_request_metrics(tmp_path):
    app = _obs_app(tmp_path)
    assert app.state.asterion.observability is not None
    with TestClient(app, raise_server_exceptions=False) as c:
        # Generate a request so the counter has a sample.
        assert c.get("/healthz").status_code == 200
        resp = c.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert "asterion_http_requests_total" in body
    assert "asterion_http_request_duration_seconds" in body
    # The /healthz request was recorded with its low-cardinality route template.
    assert 'route="/healthz"' in body


@_needs_prom
def test_tracing_backend_active_when_otel_installed(tmp_path):
    pytest.importorskip("opentelemetry")
    obs = _obs_app(tmp_path).state.asterion.observability
    assert obs.tracing_available is True
    # Opening a span must not raise even without a configured exporter.
    with obs.span("unit", {"x": 1}) as span:
        assert span is not None
