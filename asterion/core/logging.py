"""Structured logging setup.

``configure_logging(config)`` is called from ``create_admin`` so apps get a
sane default logger out of the box. Library code never calls
``logging.basicConfig`` itself — it just uses ``logging.getLogger(__name__)``.

When ``config.log_json`` is True, a minimal JSON formatter is installed:
one line per log record, with ``timestamp``, ``level``, ``logger``,
``message``, plus ``request_id``, ``actor_user_id`` and ``tenant_id``
when present on the record's ``extra``. Any payload value that might
contain secrets MUST be passed through
:func:`asterion.security.sanitize.sanitize_payload` before logging.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from asterion.core.config import CoreAdminConfig


_CONTEXTUAL_FIELDS = (
    "request_id",
    "actor_user_id",
    "tenant_id",
    "method",
    "path",
    "status_code",
    "duration_ms",
)


class JSONFormatter(logging.Formatter):
    """One-line-per-record JSON. Picks up ``extra`` fields the rest of the
    framework attaches via ``logger.info("...", extra={"request_id": ...})``."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _CONTEXTUAL_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_PLAIN_FORMAT = "%(asctime)s %(levelname)s %(name)s — %(message)s"


def configure_logging(config: CoreAdminConfig) -> None:
    """Install a single console handler on the root logger with the
    configured level + format. Idempotent: existing handlers attached by
    this function are replaced; foreign handlers (from uvicorn, etc.) are
    left in place.
    """
    root = logging.getLogger()
    root.setLevel(config.log_level.upper())

    # Remove only handlers we installed before (keep uvicorn's own).
    for handler in list(root.handlers):
        if getattr(handler, "_asterion_owned", False):
            root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    handler._asterion_owned = True  # type: ignore[attr-defined]
    if config.log_json:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter(_PLAIN_FORMAT))
    root.addHandler(handler)
