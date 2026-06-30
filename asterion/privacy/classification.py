"""PII classification registry (roadmap G1 — foundation).

``sanitize_payload`` only knows *secret* keys (passwords, tokens). It has no
concept of **personal data** (email, name, IP, behavioural records), so data
minimisation, anonymisation, subject export and PII-aware audit redaction
(G2/G5/G7/G8) have nothing systematic to build on. This module supplies that
missing vocabulary: a registry that maps a field name to a :class:`PIICategory`.

It deliberately mirrors
:class:`asterion.security.protected_fields.ProtectedFieldRegistry`:

* a module-level singleton (PII classification is a cross-cutting concern),
* :meth:`PIIFieldRegistry.register` / :meth:`freeze` lifecycle so extensions can
  contribute their own classified fields before ``create_admin`` locks it,
* :func:`reset_for_tests` for a clean per-session slate.

Keying is by **bare field name** (like the protected-field set and the audit
``changes`` dict that G7 will consume). Per-table precision can be layered on
later without changing the call sites that only have a column name to go on.

This is foundation only: nothing consumes the registry yet. G5/G7 (audit
redaction) and G2/G8 (anonymiser / export) are the first consumers.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable, Mapping


class PIICategory(enum.Enum):
    """Coarse personal-data categories (GDPR-flavoured).

    Kept intentionally small — enough to drive redaction/anonymisation policy
    without modelling the full Art. 9 taxonomy.
    """

    #: Names, user ids, anything that directly identifies a person.
    IDENTITY = "identity"
    #: Contact data: email, phone, address.
    CONTACT = "contact"
    #: Behavioural / activity data — the employee-monitoring-sensitive class
    #: (e.g. time-tracking punches, audit value diffs). §26 BDSG / Art. 88.
    BEHAVIORAL = "behavioral"
    #: Special-category data (Art. 9): health, religion, union membership, etc.
    SENSITIVE = "sensitive"


#: Default seed for a fresh registry — framework-owned PII only. Apps and
#: extensions add their own domain fields (e.g. an employee ``pin``, address
#: columns) via ``register`` / the future ``register_pii_fields`` hook.
DEFAULT_PII_FIELDS: Mapping[str, PIICategory] = {
    "email": PIICategory.CONTACT,
    "full_name": PIICategory.IDENTITY,
    # The audit ``actor_label`` carries the actor's email in clear text, and
    # ``ip_address`` is personal data under the GDPR — classify both so the
    # G2 anonymiser / G7 redaction can find them.
    "actor_label": PIICategory.CONTACT,
    "ip_address": PIICategory.CONTACT,
}


class RegistryFrozenError(RuntimeError):
    """Raised when ``register`` is called on a frozen registry."""


class PIIFieldRegistry:
    """Maps field names to their :class:`PIICategory`.

    Use :func:`get_pii_registry` for the module-level singleton; construct a
    fresh instance only in tests or isolated tools.
    """

    __slots__ = ("_fields", "_frozen")

    def __init__(self, *, defaults: Mapping[str, PIICategory] = DEFAULT_PII_FIELDS) -> None:
        self._fields: dict[str, PIICategory] = dict(defaults)
        self._frozen: bool = False

    def register(self, name: str, category: PIICategory) -> None:
        """Classify ``name`` under ``category``.

        Re-registering a name overwrites its category (last writer wins) so an
        app can re-classify a framework default — e.g. promote a field to
        ``SENSITIVE``. Raises once the registry is frozen.
        """
        if self._frozen:
            raise RegistryFrozenError(
                "PIIFieldRegistry is frozen — contribute fields before create_admin finishes setup."
            )
        if not isinstance(name, str) or not name:
            raise ValueError(f"PII field name must be a non-empty str, got {name!r}")
        if not isinstance(category, PIICategory):
            raise ValueError(f"category must be a PIICategory, got {category!r}")
        self._fields[name] = category

    def register_many(self, mapping: Mapping[str, PIICategory]) -> None:
        for name, category in mapping.items():
            self.register(name, category)

    def freeze(self) -> None:
        """Lock the registry against further modification."""
        self._frozen = True

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    def category_of(self, name: object) -> PIICategory | None:
        """Return the category for ``name``, or ``None`` if unclassified."""
        if not isinstance(name, str):
            return None
        return self._fields.get(name)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._fields

    def names(self) -> frozenset[str]:
        return frozenset(self._fields)

    def names_in(self, *categories: PIICategory) -> frozenset[str]:
        """Field names classified under any of ``categories``.

        Backs the behavioural-detail default (G5): a caller can ask for every
        ``BEHAVIORAL`` field to decide whether to keep value diffs in the audit.
        """
        wanted = set(categories)
        return frozenset(name for name, cat in self._fields.items() if cat in wanted)

    def as_mapping(self) -> Mapping[str, PIICategory]:
        return dict(self._fields)


_singleton: PIIFieldRegistry = PIIFieldRegistry()


def get_pii_registry() -> PIIFieldRegistry:
    """Return the module-level singleton."""
    return _singleton


def reset_for_tests(defaults: Iterable[tuple[str, PIICategory]] | None = None) -> None:
    """Replace the singleton with a fresh, unfrozen registry.

    Intended for test setup. Pass ``defaults`` to start from a custom seed
    instead of :data:`DEFAULT_PII_FIELDS`.
    """
    global _singleton
    if defaults is None:
        _singleton = PIIFieldRegistry()
    else:
        _singleton = PIIFieldRegistry(defaults=dict(defaults))
