"""Generic notifier SPI (Roadmap P4.5).

Why this exists
---------------

Until P4 the framework had a single typed notifier hook:
:class:`~asterion.auth.password_reset.PasswordResetNotifier`. That
worked for one event but didn't compose — every future event-emitting
flow would have grown its own ad-hoc hook with its own ``create_admin``
keyword. This module introduces:

* a marker Protocol :class:`Notifier` every typed notifier extends
  (lets ``isinstance`` and registries treat them uniformly);
* a :class:`NotifierRegistry` that stores typed notifiers and lets
  the framework + extensions look them up by Protocol class;
* a runtime attribute ``runtime.notifiers`` populated by
  ``create_admin`` (the explicit ``password_reset_notifier=`` keyword
  still works — it's registered into the registry on construction).

Adding a new notification type
------------------------------

1. Define your Protocol in your module: ``class WelcomeNotifier(Notifier,
   Protocol): async def send_welcome(...) -> None``.
2. From the publisher (e.g. a user-creation route), call
   ``runtime.notifiers.get(WelcomeNotifier)``. ``None`` means the app
   hasn't wired one — treat it as "no-op".
3. Apps register an implementation via
   ``runtime.notifiers.register(MyWelcomeNotifier())`` during their
   extension setup phase or before ``create_admin`` returns.

No global event bus — each Protocol is a named contract, not a
fan-out subscription. That keeps the dispatch explicit.
"""

from __future__ import annotations

from typing import Any, Protocol, TypeVar, runtime_checkable


@runtime_checkable
class Notifier(Protocol):
    """Marker Protocol for every typed notifier in the framework.

    The Protocol itself is intentionally empty — concrete notifier
    types extend it AND define the actual ``async def send_*`` method
    they own. ``runtime_checkable`` lets the registry verify at
    registration time that a thing handed in is at least nominally a
    notifier.
    """


T = TypeVar("T", bound=Notifier)


class NotifierRegistry:
    """Stores typed notifiers keyed by Protocol class.

    Look-up is by the exact Protocol type the caller asks for — a
    publisher that wants a :class:`PasswordResetNotifier` calls
    ``registry.get(PasswordResetNotifier)``. The registry returns
    ``None`` when nothing is registered for that protocol so the
    publisher can treat "no notifier configured" as a no-op rather
    than a failure.

    Registration accepts any object that satisfies the Protocol
    structurally; the protocol-class arg is the lookup key.
    """

    def __init__(self) -> None:
        self._by_type: dict[type[Notifier], Notifier] = {}

    def register(
        self,
        protocol_type: type[T],
        notifier: T,
    ) -> None:
        """Register ``notifier`` under ``protocol_type``.

        Re-registering the same ``protocol_type`` overwrites the prior
        entry — apps that wire a notifier in two places (e.g. the
        explicit ``create_admin`` keyword and an extension's setup
        hook) get last-writer-wins semantics. The override is loud
        only if you grep for ``register``; tests and prod won't
        silently warn.
        """
        if not isinstance(notifier, protocol_type):
            raise TypeError(f"{type(notifier).__name__} does not satisfy {protocol_type.__name__}")
        self._by_type[protocol_type] = notifier

    def get(self, protocol_type: type[T]) -> T | None:
        """Return the registered notifier for ``protocol_type`` or
        ``None``. Publishers MUST check for ``None`` — the absence is
        the documented "no-op" state."""
        return self._by_type.get(protocol_type)  # type: ignore[return-value]

    def __contains__(self, protocol_type: Any) -> bool:
        return protocol_type in self._by_type

    def __len__(self) -> int:
        return len(self._by_type)
