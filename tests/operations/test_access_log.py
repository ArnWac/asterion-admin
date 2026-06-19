"""Tests for AccessLogMiddleware (PR-11 hotfix #3 / plan §PR-4)."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from asterion import CoreAdminConfig, create_admin
from asterion.core.middleware import REQUEST_ID_HEADER


@pytest.fixture
def app(tmp_path):
    return create_admin(
        config=CoreAdminConfig(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'access-log.db'}",
            secret_key="test-access-log-secret",
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        )
    )


@pytest.fixture
def client(app):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _find_request_record(caplog) -> logging.LogRecord:
    matches = [r for r in caplog.records if r.name == "asterion.access" and r.message == "request"]
    assert matches, "Expected at least one access-log record"
    return matches[-1]


# --- basic record shape ---


def test_access_log_writes_one_record_per_request(client, caplog):
    # Lift root + the access logger to INFO. Prior tests in the suite may
    # have left the root logger at a higher level (configure_logging only
    # sets root, doesn't restore).
    caplog.set_level(logging.INFO)
    caplog.set_level(logging.INFO, logger="asterion.access")
    client.get("/healthz")

    requests = [r for r in caplog.records if r.name == "asterion.access"]
    assert len(requests) == 1


def test_access_log_includes_request_id_method_path_status(client, caplog):
    # Lift root + the access logger to INFO. Prior tests in the suite may
    # have left the root logger at a higher level (configure_logging only
    # sets root, doesn't restore).
    caplog.set_level(logging.INFO)
    caplog.set_level(logging.INFO, logger="asterion.access")
    client.get("/healthz", headers={REQUEST_ID_HEADER: "trace-abc"})
    rec = _find_request_record(caplog)

    assert rec.request_id == "trace-abc"
    assert rec.method == "GET"
    assert rec.path == "/healthz"
    assert rec.status_code == 200


def test_access_log_includes_duration_ms(client, caplog):
    # Lift root + the access logger to INFO. Prior tests in the suite may
    # have left the root logger at a higher level (configure_logging only
    # sets root, doesn't restore).
    caplog.set_level(logging.INFO)
    caplog.set_level(logging.INFO, logger="asterion.access")
    client.get("/healthz")
    rec = _find_request_record(caplog)
    assert isinstance(rec.duration_ms, (int, float))
    assert rec.duration_ms >= 0


def test_access_log_status_reflects_error_responses(client, caplog):
    # Lift root + the access logger to INFO. Prior tests in the suite may
    # have left the root logger at a higher level (configure_logging only
    # sets root, doesn't restore).
    caplog.set_level(logging.INFO)
    caplog.set_level(logging.INFO, logger="asterion.access")
    client.get("/no-such-route")
    rec = _find_request_record(caplog)
    assert rec.status_code == 404


# --- robustness ---


def test_log_extra_skips_actor_when_id_access_raises():
    """A detached ORM object on request.state.current_user raises when
    its ``id`` attribute is accessed. _log_extra must swallow that so a
    successful response is never turned into a 500 by a logging failure."""
    from types import SimpleNamespace

    from asterion.core.middleware import _log_extra

    class _Boom:
        @property
        def id(self):
            raise RuntimeError("detached instance")

    fake_state = SimpleNamespace(current_user=_Boom(), request_id="rid-1")
    fake_request = SimpleNamespace(
        state=fake_state,
        method="GET",
        url=SimpleNamespace(path="/x"),
    )

    extra = _log_extra(fake_request, status_code=200, duration_ms=12.3)
    assert extra["request_id"] == "rid-1"
    assert extra["method"] == "GET"
    assert extra["path"] == "/x"
    assert extra["status_code"] == 200
    assert extra["duration_ms"] == 12.3
    assert "actor_user_id" not in extra


def test_log_extra_skips_tenant_when_id_access_raises():
    from types import SimpleNamespace

    from asterion.core.middleware import _log_extra

    class _BoomTenant:
        @property
        def id(self):
            raise RuntimeError("detached")

    fake_state = SimpleNamespace(tenant=_BoomTenant(), request_id=None)
    fake_request = SimpleNamespace(state=fake_state, method="GET", url=SimpleNamespace(path="/y"))
    extra = _log_extra(fake_request, status_code=200, duration_ms=1.0)
    assert "tenant_id" not in extra


def test_log_extra_includes_actor_and_tenant_when_safely_readable():
    import uuid as _uuid
    from types import SimpleNamespace

    from asterion.core.middleware import _log_extra

    aid = _uuid.uuid4()
    tid = _uuid.uuid4()

    fake_state = SimpleNamespace(
        current_user=SimpleNamespace(id=aid),
        tenant=SimpleNamespace(id=tid),
        request_id="rid",
    )
    fake_request = SimpleNamespace(state=fake_state, method="POST", url=SimpleNamespace(path="/p"))
    extra = _log_extra(fake_request, status_code=201, duration_ms=4.0)
    assert extra["actor_user_id"] == str(aid)
    assert extra["tenant_id"] == str(tid)
