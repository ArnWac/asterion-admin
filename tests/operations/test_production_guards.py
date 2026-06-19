"""Production-mode config guards (plan §PR-10)."""

from __future__ import annotations

import pytest

from asterion import CoreAdminConfig


def _make(**overrides) -> CoreAdminConfig:
    base = dict(
        database_url="postgresql+asyncpg://u:p@localhost:5432/db",
        secret_key="x" * 32,
        environment="production",
    )
    base.update(overrides)
    return CoreAdminConfig(**base)


# --- environment field ---


def test_default_environment_is_development():
    cfg = CoreAdminConfig(
        database_url="sqlite+aiosqlite:///:memory:",
        secret_key="x" * 16,
    )
    assert cfg.environment == "development"


def test_invalid_environment_rejected():
    with pytest.raises(ValueError, match="environment must be one of"):
        CoreAdminConfig(
            database_url="sqlite+aiosqlite:///:memory:",
            secret_key="x" * 16,
            environment="staging",  # type: ignore[arg-type]
        ).validate()


# --- production: secret_key length ---


def test_short_secret_rejected_in_production():
    cfg = _make(secret_key="x" * 16)
    with pytest.raises(ValueError, match="at least 32"):
        cfg.validate()


def test_long_secret_accepted_in_production():
    _make(secret_key="x" * 32).validate()


def test_short_secret_allowed_in_development():
    CoreAdminConfig(
        database_url="sqlite+aiosqlite:///:memory:",
        secret_key="x" * 16,
        environment="development",
    ).validate()


# --- production: debug=True ---


def test_debug_rejected_in_production():
    cfg = _make(debug=True)
    with pytest.raises(ValueError, match="debug=True is not allowed in production"):
        cfg.validate()


def test_debug_allowed_in_development():
    CoreAdminConfig(
        database_url="sqlite+aiosqlite:///:memory:",
        secret_key="x" * 16,
        debug=True,
        environment="development",
    ).validate()


# --- production: SQLite rejected ---


def test_sqlite_rejected_in_production():
    cfg = _make(database_url="sqlite+aiosqlite:///:memory:")
    with pytest.raises(ValueError, match="SQLite is not allowed in production"):
        cfg.validate()


def test_sqlite_allowed_in_development():
    CoreAdminConfig(
        database_url="sqlite+aiosqlite:///:memory:",
        secret_key="x" * 16,
        environment="development",
    ).validate()


def test_postgres_accepted_in_production():
    _make(database_url="postgresql+asyncpg://u:p@localhost:5432/db").validate()


# --- to_safe_dict surfaces environment ---


def test_to_safe_dict_includes_environment():
    cfg = _make()
    dumped = cfg.to_safe_dict()
    assert dumped["environment"] == "production"
