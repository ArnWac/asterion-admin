"""Recursive secret redaction for logs, audit records, and error contexts.

Never log raw Authorization headers or request bodies without first passing them
through ``sanitize_payload``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REDACTED = "***REDACTED***"

SENSITIVE_KEY_TOKENS: frozenset[str] = frozenset(
    {
        "password",
        "new_password",
        "current_password",
        "hashed_password",
        "password_hash",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "secret_key",
        "authorization",
        "cookie",
        "set_cookie",
        "api_key",
        "apikey",
        "private_key",
    }
)

_SENSITIVE_PATTERN = re.compile(
    r"(?<![a-z0-9])(?:"
    + "|".join(sorted(SENSITIVE_KEY_TOKENS, key=len, reverse=True))
    + r")(?![a-z0-9])",
    re.IGNORECASE,
)


def _is_sensitive_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    normalized = key.replace("-", "_")
    return bool(_SENSITIVE_PATTERN.search(normalized))


def sanitize_payload(payload: Any) -> Any:
    """Return a deep copy of ``payload`` with sensitive values redacted.

    - Dicts/Mappings: scalar values under sensitive keys become ``REDACTED``.
      Nested dicts/lists are recursed into so other non-sensitive entries
      inside them are preserved.
    - Lists/tuples: recurse element-wise.
    - Other types: returned unchanged.
    """
    if isinstance(payload, Mapping):
        sanitized: dict[Any, Any] = {}
        for key, value in payload.items():
            if _is_sensitive_key(key) and not isinstance(value, (Mapping, list, tuple)):
                sanitized[key] = REDACTED
            else:
                sanitized[key] = sanitize_payload(value)
        return sanitized
    if isinstance(payload, list):
        return [sanitize_payload(item) for item in payload]
    if isinstance(payload, tuple):
        return tuple(sanitize_payload(item) for item in payload)
    return payload
