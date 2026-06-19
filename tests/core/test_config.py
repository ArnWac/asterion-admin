"""Tests for CoreAdminConfig."""

from __future__ import annotations

import pytest

from asterion.core.config import CoreAdminConfig


def _make(**kwargs):
    return CoreAdminConfig(
        database_url=kwargs.pop("database_url", "sqlite+aiosqlite:///:memory:"),
        secret_key=kwargs.pop("secret_key", "my-secret"),
        **kwargs,
    )


def test_valid_config():
    cfg = _make()
    cfg.validate()


def test_empty_database_url_raises():
    cfg = _make(database_url="")
    with pytest.raises(ValueError, match="database_url"):
        cfg.validate()


def test_empty_secret_key_raises():
    cfg = _make(secret_key="")
    with pytest.raises(ValueError, match="secret_key"):
        cfg.validate()


def test_to_safe_dict_excludes_secrets():
    cfg = _make()
    safe = cfg.to_safe_dict()
    assert "database_url" not in safe
    assert "secret_key" not in safe


def test_from_env(monkeypatch):
    monkeypatch.setenv("ASTERION_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("ASTERION_SECRET_KEY", "env-secret")
    cfg = CoreAdminConfig.from_env()
    assert cfg.database_url == "sqlite+aiosqlite:///:memory:"
    assert cfg.secret_key == "env-secret"


def test_default_values():
    cfg = _make()
    assert cfg.jwt_algorithm == "HS256"
    assert cfg.access_token_expire_minutes == 60
    assert cfg.enable_multi_tenant is True
