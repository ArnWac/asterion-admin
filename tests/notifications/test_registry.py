"""Generic notifier SPI + NotifierRegistry (Roadmap P4.5).

Covers:
* basic register / get / contains semantics;
* typed registration: a notifier that doesn't satisfy the requested
  Protocol is rejected with TypeError at registration time;
* lookup returns ``None`` (not error) when nothing is registered —
  publishers treat absent notifiers as no-ops;
* re-registration overwrites cleanly (last-writer-wins);
* end-to-end via ``create_admin``: the explicit
  ``password_reset_notifier=`` is auto-mirrored into the runtime
  registry so generic-lookup callers can find it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pytest
from fastapi import Request

from asterion import CoreAdminConfig, create_admin
from asterion.auth.password_reset import (
    LoggingPasswordResetNotifier,
    PasswordResetNotifier,
)
from asterion.notifications import Notifier, NotifierRegistry

SECRET = "x" * 64


# ---------------------------------------------------------------------------
# Test-only protocols + impls
# ---------------------------------------------------------------------------


@runtime_checkable
class _WelcomeNotifier(Notifier, Protocol):
    async def send_welcome(self, *, email: str) -> None: ...


class _RecordingWelcome:
    """Capture-only impl used to assert the registry returned the
    right instance — not the right *type*."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_welcome(self, *, email: str) -> None:
        self.sent.append(email)


class _NotAWelcomeNotifier:
    """Has no matching method — must fail isinstance(_, _WelcomeNotifier)
    at registration."""


# ---------------------------------------------------------------------------
# Registry semantics
# ---------------------------------------------------------------------------


def test_registry_starts_empty():
    reg = NotifierRegistry()
    assert len(reg) == 0
    assert reg.get(_WelcomeNotifier) is None
    assert _WelcomeNotifier not in reg


def test_register_then_get_returns_same_instance():
    reg = NotifierRegistry()
    impl = _RecordingWelcome()
    reg.register(_WelcomeNotifier, impl)
    assert reg.get(_WelcomeNotifier) is impl
    assert _WelcomeNotifier in reg
    assert len(reg) == 1


def test_register_rejects_object_not_matching_protocol():
    """A notifier that doesn't implement the named methods can't be
    registered under that protocol — fails fast at registration, not
    at first dispatch."""
    reg = NotifierRegistry()
    with pytest.raises(TypeError, match="does not satisfy"):
        reg.register(_WelcomeNotifier, _NotAWelcomeNotifier())  # type: ignore[arg-type]


def test_re_registration_overwrites():
    """Last-writer-wins so extensions can rebind a notifier the host
    app installed — explicit and easy to grep for."""
    reg = NotifierRegistry()
    first = _RecordingWelcome()
    second = _RecordingWelcome()
    reg.register(_WelcomeNotifier, first)
    reg.register(_WelcomeNotifier, second)
    assert reg.get(_WelcomeNotifier) is second
    assert len(reg) == 1  # not 2 — same key


# ---------------------------------------------------------------------------
# Protocol marker
# ---------------------------------------------------------------------------


def test_password_reset_notifier_is_a_notifier():
    """PasswordResetNotifier must extend the Notifier marker so the
    registry's runtime-checkable guard accepts it."""
    impl = LoggingPasswordResetNotifier()
    assert isinstance(impl, Notifier)
    assert isinstance(impl, PasswordResetNotifier)


# ---------------------------------------------------------------------------
# Auto-registration via create_admin
# ---------------------------------------------------------------------------


def test_create_admin_auto_registers_password_reset_notifier(tmp_path):
    """The explicit ``password_reset_notifier=`` keyword is mirrored
    into ``runtime.notifiers`` so generic-lookup callers don't need
    to know about the ad-hoc attribute."""
    app = create_admin(
        config=CoreAdminConfig(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'a.db'}",
            secret_key=SECRET,
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        ),
    )
    runtime = app.state.asterion
    found = runtime.notifiers.get(PasswordResetNotifier)
    assert found is not None
    # Default is the dev-only logging notifier — pin the type rather
    # than identity so a future "auto-pick" default change is loud.
    assert isinstance(found, LoggingPasswordResetNotifier)


def test_create_admin_with_explicit_notifier_registers_that_instance(tmp_path):
    """A real app passes its own notifier; the registry must hand
    back exactly that instance — not a wrapper, not a copy."""

    class MyNotifier:
        async def send_reset(
            self,
            *,
            email: str,
            token: str,
            request: Request | None = None,
        ) -> None:
            pass

    explicit = MyNotifier()
    app = create_admin(
        config=CoreAdminConfig(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'b.db'}",
            secret_key=SECRET,
            enable_multi_tenant=False,
            enable_builtin_ui=False,
            enable_builtin_admins=False,
        ),
        password_reset_notifier=explicit,
    )
    runtime = app.state.asterion
    assert runtime.notifiers.get(PasswordResetNotifier) is explicit
    # Old attribute still works (backwards compat).
    assert runtime.password_reset_notifier is explicit
