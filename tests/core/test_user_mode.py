"""§9 ``CoreAdminConfig.user_mode`` flag tests.

Validates:
* Default value is ``"builtin"``.
* Both ``"builtin"`` and ``"external"`` validate cleanly.
* Unknown values raise.
* ``from_env`` picks up ``ASTERION_USER_MODE``.
* ``to_safe_dict`` surfaces the value (it's not a secret).
* ``create_admin`` boots in ``"builtin"`` mode without explicit
  providers (the legacy default path).
* ``create_admin`` refuses to boot in ``"external"`` mode without an
  explicit ``auth_provider``.
* ``create_admin`` accepts ``"external"`` mode when an
  ``auth_provider`` is supplied.
"""

from __future__ import annotations

import pytest

from asterion import create_admin
from asterion.core.config import CoreAdminConfig


def _cfg(**kwargs) -> CoreAdminConfig:
    return CoreAdminConfig(
        database_url=kwargs.pop("database_url", "sqlite+aiosqlite:///:memory:"),
        secret_key=kwargs.pop("secret_key", "x" * 64),
        environment=kwargs.pop("environment", "development"),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Config-level behaviour
# ---------------------------------------------------------------------------


def test_user_mode_defaults_to_builtin():
    cfg = _cfg()
    assert cfg.user_mode == "builtin"


def test_user_mode_builtin_validates():
    _cfg(user_mode="builtin").validate()


def test_user_mode_external_validates():
    _cfg(user_mode="external").validate()


def test_user_mode_unknown_value_rejected():
    cfg = _cfg(user_mode="hybrid")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="user_mode"):
        cfg.validate()


def test_to_safe_dict_includes_user_mode():
    safe = _cfg(user_mode="external").to_safe_dict()
    assert safe["user_mode"] == "external"


def test_from_env_picks_up_user_mode(monkeypatch):
    monkeypatch.setenv("ASTERION_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("ASTERION_SECRET_KEY", "x" * 64)
    monkeypatch.setenv("ASTERION_USER_MODE", "external")
    cfg = CoreAdminConfig.from_env()
    assert cfg.user_mode == "external"


def test_from_env_rejects_unknown_user_mode(monkeypatch):
    monkeypatch.setenv("ASTERION_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("ASTERION_SECRET_KEY", "x" * 64)
    monkeypatch.setenv("ASTERION_USER_MODE", "nonsense")
    with pytest.raises(ValueError, match="ASTERION_USER_MODE"):
        CoreAdminConfig.from_env()


# ---------------------------------------------------------------------------
# create_admin integration
# ---------------------------------------------------------------------------


class _FakeAuthProvider:
    """Minimal AuthProvider stub — returns anonymous for every
    request. Sufficient for the "external mode boots" smoke test."""

    async def authenticate_request(self, request):
        return None


def test_builtin_mode_boots_without_providers():
    """``user_mode='builtin'`` is the quickstart default and must boot
    with zero provider overrides — the built-in JWT + SQLAlchemy stack
    backs every concern."""
    app = create_admin(config=_cfg(user_mode="builtin"))
    assert app is not None


def test_external_mode_refuses_without_auth_provider():
    with pytest.raises(ValueError, match="user_mode='external' requires"):
        create_admin(config=_cfg(user_mode="external"))


def test_external_mode_accepts_with_auth_provider():
    """Explicit ``auth_provider`` is the smallest "you really meant
    external mode" gate. Other providers can stay builtin for staged
    migrations."""
    app = create_admin(
        config=_cfg(user_mode="external"),
        auth_provider=_FakeAuthProvider(),
    )
    runtime = app.state.asterion
    # Confirm the fake provider made it onto the runtime, not the
    # builtin one — proves the override chain ran.
    assert runtime.providers.auth.__class__.__name__ == "_FakeAuthProvider"
