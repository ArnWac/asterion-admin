"""Optional observability: OpenTelemetry tracing + Prometheus metrics (G20).

Both backends are **optional dependencies** and the whole module degrades to a
no-op when they are absent, so importing/enabling observability never forces a
dependency on a deployment that doesn't want it:

* **Tracing** uses ``opentelemetry-api``. A span is opened per request with
  ``http.*`` / ``tenant.slug`` / ``actor.user_id`` attributes. Export is the
  operator's concern — configure an OTel SDK + exporter; without one the spans
  are no-ops (the API returns a no-op tracer), still without error.
* **Metrics** use ``prometheus-client``: a request counter + a duration
  histogram, labelled by ``method`` / ``route`` / ``status`` (NOT tenant — that
  would explode cardinality; tenant lives on the span instead). Exposed at
  ``/metrics`` in the Prometheus text format.

Each :class:`Observability` owns its **own** Prometheus ``CollectorRegistry`` so
constructing several apps in one process (tests) never hits the global default
registry's duplicate-timeseries guard.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Iterator, Mapping
from typing import Any

from fastapi import APIRouter, Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware

from asterion.tenancy.resolver import _extract_slug

#: Prometheus histogram buckets (seconds) tuned for HTTP request latency.
_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class Observability:
    """Holds the (optional) tracer + metric instruments for one app.

    Construct via :func:`build_observability`. All methods are safe no-ops for
    whichever backend is unavailable.
    """

    def __init__(
        self,
        *,
        registry: Any = None,
        request_counter: Any = None,
        request_duration: Any = None,
        tracer: Any = None,
    ) -> None:
        self._registry = registry
        self._request_counter = request_counter
        self._request_duration = request_duration
        self._tracer = tracer

    @property
    def metrics_available(self) -> bool:
        return self._registry is not None

    @property
    def tracing_available(self) -> bool:
        return self._tracer is not None

    @contextlib.contextmanager
    def span(self, name: str, attributes: Mapping[str, Any] | None = None) -> Iterator[Any]:
        """Open a span (no-op yielding ``None`` when tracing is unavailable)."""
        if self._tracer is None:
            yield None
            return
        with self._tracer.start_as_current_span(name) as span:
            if attributes:
                for key, value in attributes.items():
                    if value is not None:
                        span.set_attribute(key, value)
            yield span

    @staticmethod
    def set_span_attributes(span: Any, attributes: Mapping[str, Any]) -> None:
        if span is None:
            return
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(key, value)

    def record_request(
        self, *, method: str, route: str, status_code: int, duration_s: float
    ) -> None:
        if self._registry is None:
            return
        status_label = str(status_code)
        self._request_counter.labels(method=method, route=route, status=status_label).inc()
        self._request_duration.labels(method=method, route=route).observe(duration_s)

    def metrics_exposition(self) -> tuple[bytes, str] | None:
        """Return ``(payload, content_type)`` for ``/metrics`` or ``None`` when
        the Prometheus backend is unavailable."""
        if self._registry is None:
            return None
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

        return generate_latest(self._registry), CONTENT_TYPE_LATEST


def _build_metrics() -> tuple[Any, Any, Any] | None:
    try:
        from prometheus_client import CollectorRegistry, Counter, Histogram
    except ImportError:
        return None
    registry = CollectorRegistry()
    counter = Counter(
        "asterion_http_requests_total",
        "Total HTTP requests handled.",
        labelnames=("method", "route", "status"),
        registry=registry,
    )
    duration = Histogram(
        "asterion_http_request_duration_seconds",
        "HTTP request duration in seconds.",
        labelnames=("method", "route"),
        buckets=_LATENCY_BUCKETS,
        registry=registry,
    )
    return registry, counter, duration


def _build_tracer(service_name: str) -> Any:
    try:
        from opentelemetry import trace
    except ImportError:
        return None
    # Returns a no-op tracer when no SDK/provider is configured — never raises,
    # so enabling tracing without an exporter is harmless.
    return trace.get_tracer(service_name)


def build_observability(*, enabled: bool, service_name: str) -> Observability | None:
    """Build an :class:`Observability` for the app, or ``None`` when disabled.

    When enabled but a backend's package is missing, that backend is simply
    inactive (no-op) — the other still works, and nothing raises.
    """
    if not enabled:
        return None
    metrics = _build_metrics()
    if metrics is None:
        registry = counter = duration = None
    else:
        registry, counter, duration = metrics
    return Observability(
        registry=registry,
        request_counter=counter,
        request_duration=duration,
        tracer=_build_tracer(service_name),
    )


def _route_template(request: Request) -> str:
    """Low-cardinality route label: the matched route's path template (e.g.
    ``/api/v1/admin/{resource}``), or ``"unmatched"`` for a 404."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    return "unmatched"


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Per-request span + metrics. Inert when no ``Observability`` is wired."""

    async def dispatch(self, request: Request, call_next):
        runtime = getattr(getattr(request.app, "state", None), "asterion", None)
        obs: Observability | None = getattr(runtime, "observability", None) if runtime else None
        if obs is None:
            return await call_next(request)

        start = time.perf_counter()
        tenant_slug = _extract_slug(request)
        with obs.span(
            "http.request",
            {
                "http.method": request.method,
                "http.target": request.url.path,
                "tenant.slug": tenant_slug,
            },
        ) as span:
            response = await call_next(request)
            route = _route_template(request)
            actor = getattr(request.state, "actor_user_id", None)
            obs.set_span_attributes(
                span,
                {
                    "http.route": route,
                    "http.status_code": response.status_code,
                    "actor.user_id": str(actor) if actor is not None else None,
                },
            )

        obs.record_request(
            method=request.method,
            route=route,
            status_code=response.status_code,
            duration_s=time.perf_counter() - start,
        )
        return response


def build_metrics_router(metrics_path: str = "/metrics") -> APIRouter:
    """Router exposing the Prometheus exposition at ``metrics_path``.

    Returns ``503`` when observability is enabled but the Prometheus backend
    isn't installed, so a scrape config gets a clear signal instead of a 404.
    """
    router = APIRouter()

    @router.get(metrics_path, include_in_schema=False)
    async def metrics(request: Request) -> Response:
        runtime = getattr(getattr(request.app, "state", None), "asterion", None)
        obs: Observability | None = getattr(runtime, "observability", None) if runtime else None
        payload = obs.metrics_exposition() if obs is not None else None
        if payload is None:
            return Response(
                content="Metrics backend not installed (pip install asterion-admin[observability]).",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                media_type="text/plain",
            )
        body, content_type = payload
        return Response(content=body, media_type=content_type)

    return router
