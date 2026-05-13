"""Webhook dispatcher — register HTTP endpoints to receive signal events.

Developer-facing: configure programmatically in your app startup or admin_config.py.
No database table, no admin UI required.

Usage::

    from adminfoundry.extensions import webhooks

    webhooks.register(
        url="https://my-service.com/hooks/adminfoundry",
        events=["post_create", "post_update", "post_delete"],
        secret="my-hmac-secret",        # HMAC-SHA256 as X-Signature-256 header
        model_filter=["articles"],      # optional — omit to receive all models
    )

Payload shape::

    {
        "event":      "post_create",
        "timestamp":  1718000000,
        "model_name": "articles",
        "object_id":  "uuid",
        "actor":      "admin@example.com",
        "changes":    {...} or null
    }

Signature verification (receiving end)::

    import hashlib, hmac
    body = request.body()
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(expected, request.headers["X-Signature-256"])

Available events:
    post_create, post_update, pre_delete, post_delete,
    post_login, post_logout, post_password_change
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Callable


class _WebhookTarget:
    __slots__ = ("url", "events", "secret", "model_filter")

    def __init__(
        self,
        url: str,
        events: list[str],
        secret: str | None,
        model_filter: list[str] | None,
    ) -> None:
        self.url = url
        self.events = set(events)
        self.secret = secret
        self.model_filter = set(model_filter) if model_filter else None

    def matches(self, event: str, model_name: str | None) -> bool:
        if event not in self.events:
            return False
        if self.model_filter is not None and model_name not in self.model_filter:
            return False
        return True


_targets: list[_WebhookTarget] = []


def register(
    url: str,
    events: list[str],
    secret: str | None = None,
    model_filter: list[str] | None = None,
) -> None:
    """Register an HTTP endpoint to receive admin signal events.

    url          — HTTP(S) endpoint to POST to
    events       — signal events to subscribe to
    secret       — when set, every request carries X-Signature-256 (HMAC-SHA256)
    model_filter — restrict to specific model names; None = all models
    """
    from adminfoundry import signals as _signals

    target = _WebhookTarget(url, events, secret, model_filter)
    _targets.append(target)

    def _make_handler(event_name: str) -> Callable:
        async def _handler(**kwargs: Any) -> None:
            if not target.matches(event_name, kwargs.get("model_name")):
                return
            await _post(target, _build_payload(event_name, kwargs))
        return _handler

    for event in events:
        _signals.connect(event, _make_handler(event))


def _build_payload(event: str, kwargs: Any) -> dict:
    obj = kwargs.get("obj")
    user = kwargs.get("user")
    return {
        "event": event,
        "timestamp": int(time.time()),
        "model_name": kwargs.get("model_name"),
        "object_id": str(getattr(obj, "id", None) or kwargs.get("object_id") or ""),
        "actor": getattr(user, "email", None),
        "changes": kwargs.get("changes"),
    }


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def _post(target: _WebhookTarget, payload: dict) -> None:
    try:
        import httpx
    except ImportError:
        raise RuntimeError(
            "adminfoundry webhooks require httpx: pip install httpx"
        )
    body = json.dumps(payload, default=str).encode()
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "X-adminfoundry-Event": payload["event"],
    }
    if target.secret:
        headers["X-Signature-256"] = _sign(body, target.secret)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(target.url, content=body, headers=headers)
    except Exception:
        pass  # webhook delivery failures must never affect the main request path


def clear() -> None:
    """Deregister all webhooks — useful in tests."""
    _targets.clear()


__all__ = ["register", "clear"]
