"""Centralized protected-field registry.

Phase 4 of the v1-providers refactor pulls the previously-hardcoded
``GLOBALLY_PROTECTED`` constant from :mod:`asterion.registry.admin`
into a registry so that extensions can contribute their own protected
fields (OAuth ``access_token`` / ``refresh_token`` / ``id_token``,
webhook signing secrets, etc.) without forking the framework.

Lifecycle:

1. ``create_admin()`` initializes the singleton registry from
   :data:`DEFAULT_PROTECTED_FIELDS`.
2. Extensions (Phase 5) call ``registry.register("oauth_access_token")``
   from their ``register_protected_fields`` hook.
3. ``create_admin()`` calls ``registry.freeze()`` after all extensions
   are configured. Subsequent ``register`` calls raise
   :class:`RegistryFrozenError`.
4. All serializer, contract, and write-payload code paths read the set
   via :attr:`asterion.registry.admin.ModelAdmin.all_protected`,
   which combines the registry with the admin's own
   ``protected_fields``.

The registry is intentionally a module-level singleton — protected
fields are inherently a global concern (a leaked OAuth token from any
admin's response is the same security failure regardless of which app
holds the registry). The :func:`reset_for_tests` hook gives test
fixtures a clean slate per session.
"""

from __future__ import annotations

from collections.abc import Iterable

#: Default set seeded into a fresh registry. These cover the core
#: framework's invariants (User password hashes, tenant salts, setup
#: codes). Extensions add to this list at startup.
DEFAULT_PROTECTED_FIELDS: frozenset[str] = frozenset(
    {
        "hashed_password",
        "password",
        "password_hash",
        "pin_hash",
        "shared_secret",
        "tenant_salt",
        "setup_code",
        "qr_bootstrap_token",
    }
)


class RegistryFrozenError(RuntimeError):
    """Raised when ``register`` is called on a frozen registry."""


class ProtectedFieldRegistry:
    """Container for the set of field names that must never leak.

    Use :func:`get_registry` to access the module-level singleton.
    Construct a fresh instance only in tests or for isolated tools.
    """

    __slots__ = ("_fields", "_frozen")

    def __init__(self, *, defaults: Iterable[str] = DEFAULT_PROTECTED_FIELDS) -> None:
        self._fields: set[str] = set(defaults)
        self._frozen: bool = False

    def register(self, *names: str) -> None:
        """Add field names to the registry. No-op for already-present names."""
        if self._frozen:
            raise RegistryFrozenError(
                "ProtectedFieldRegistry is frozen — extensions must register "
                "fields before create_admin finishes setup."
            )
        for name in names:
            if not isinstance(name, str) or not name:
                raise ValueError(f"Protected field name must be non-empty str, got {name!r}")
            self._fields.add(name)

    def freeze(self) -> None:
        """Lock the registry against further modifications."""
        self._frozen = True

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._fields

    def as_frozenset(self) -> frozenset[str]:
        return frozenset(self._fields)


_singleton: ProtectedFieldRegistry = ProtectedFieldRegistry()


def get_registry() -> ProtectedFieldRegistry:
    """Return the module-level singleton."""
    return _singleton


def reset_for_tests() -> None:
    """Replace the singleton with a fresh, unfrozen registry.

    Intended for test session setup — should not be called from
    production code paths.
    """
    global _singleton
    _singleton = ProtectedFieldRegistry()
