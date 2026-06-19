"""Tests for asterion.core.logging."""

from __future__ import annotations

import io
import json
import logging

import pytest

from asterion import CoreAdminConfig
from asterion.core.logging import JSONFormatter, configure_logging


def _make_config(**kw):
    base = dict(
        database_url="sqlite+aiosqlite:///:memory:",
        secret_key="test-log-secret",
        enable_multi_tenant=False,
        enable_builtin_ui=False,
        enable_builtin_admins=False,
    )
    base.update(kw)
    return CoreAdminConfig(**base)


def _capture_root_handler():
    """Replace the asterion-owned root handler with one that writes
    into an in-memory buffer. Returns the buffer + the new handler."""
    root = logging.getLogger()
    target = next(
        (h for h in root.handlers if getattr(h, "_asterion_owned", False)),
        None,
    )
    if target is None:
        pytest.skip("No asterion-owned handler installed yet.")
    buf = io.StringIO()
    target.stream = buf
    return buf


def test_configure_logging_sets_level():
    configure_logging(_make_config(log_level="WARNING"))
    assert logging.getLogger().level == logging.WARNING


def test_configure_logging_is_idempotent():
    configure_logging(_make_config(log_level="INFO"))
    handlers_before = [
        h for h in logging.getLogger().handlers if getattr(h, "_asterion_owned", False)
    ]
    configure_logging(_make_config(log_level="INFO"))
    handlers_after = [
        h for h in logging.getLogger().handlers if getattr(h, "_asterion_owned", False)
    ]
    assert len(handlers_before) == len(handlers_after) == 1


def test_invalid_log_level_rejected_in_validate():
    with pytest.raises(ValueError, match="log_level"):
        CoreAdminConfig(
            database_url="sqlite+aiosqlite:///:memory:",
            secret_key="x" * 32,
            log_level="LOUD",
        ).validate()


# --- JSON formatter ---


def test_json_formatter_emits_basic_fields():
    rec = logging.LogRecord(
        name="asterion.test",
        level=logging.INFO,
        pathname="x.py",
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    line = JSONFormatter().format(rec)
    payload = json.loads(line)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "asterion.test"
    assert payload["message"] == "hello world"


def test_json_formatter_includes_contextual_fields():
    rec = logging.LogRecord(
        name="asterion.test",
        level=logging.INFO,
        pathname="x.py",
        lineno=1,
        msg="touched",
        args=(),
        exc_info=None,
    )
    rec.request_id = "rid-123"
    rec.tenant_id = "tenant-abc"
    line = JSONFormatter().format(rec)
    payload = json.loads(line)
    assert payload["request_id"] == "rid-123"
    assert payload["tenant_id"] == "tenant-abc"


def test_configure_logging_json_mode_uses_json_formatter():
    configure_logging(_make_config(log_json=True, log_level="DEBUG"))
    root = logging.getLogger()
    owned = [h for h in root.handlers if getattr(h, "_asterion_owned", False)]
    assert owned, "expected an asterion-owned handler"
    assert isinstance(owned[0].formatter, JSONFormatter)
