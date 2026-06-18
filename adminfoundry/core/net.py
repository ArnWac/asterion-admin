"""Client-IP resolution behind reverse proxies (Review R16).

`request.client.host` is the *direct peer*. Behind a reverse proxy that is the
proxy's IP, not the real client — which silently breaks the tenant IP allowlist
and records the wrong audit IP. `X-Forwarded-For` carries the chain, but a
client can forge it, so it is only trustworthy for the hops added by proxies
*you* operate. Hence the trust is explicit (`trusted_proxy_count`); the default
of 0 ignores the header entirely.
"""

from __future__ import annotations

from starlette.requests import Request


def client_ip(request: Request, *, trusted_proxy_count: int = 0) -> str | None:
    """Best-effort real client IP.

    With ``trusted_proxy_count == 0`` (default) returns the direct peer and
    ignores ``X-Forwarded-For``. With ``N`` trusted proxies, returns the N-th
    entry from the right of ``X-Forwarded-For`` (the address the outermost
    trusted proxy observed); entries further left are client-controlled and not
    trusted. Falls back to the peer when the header is absent/empty.
    """
    peer = request.client.host if request.client else None
    if trusted_proxy_count <= 0:
        return peer
    xff = request.headers.get("x-forwarded-for")
    if not xff:
        return peer
    parts = [p.strip() for p in xff.split(",") if p.strip()]
    if not parts:
        return peer
    idx = min(trusted_proxy_count, len(parts))
    return parts[-idx]


def request_client_ip(request: Request) -> str | None:
    """:func:`client_ip` using the app's configured ``trusted_proxy_count``."""
    count = 0
    runtime = getattr(getattr(request, "app", None), "state", None)
    runtime = getattr(runtime, "adminfoundry", None)
    if runtime is not None:
        count = getattr(runtime.config, "trusted_proxy_count", 0) or 0
    return client_ip(request, trusted_proxy_count=count)
