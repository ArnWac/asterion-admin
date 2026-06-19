"""Minimal ContractContributionRegistry — namespaced fragments for the
contract API.

Phase 5 ships the container; Phase 6 hooks it into the ``/_contract``
endpoint so the contributed fragments appear under an ``extensions``
top-level key, e.g.::

    {
      "contract_version": "1.0",
      "models": [...],
      "extensions": {
        "auth_oauth": {
          "providers": [
            {"id": "google", "label": "Google", "login_url": "/api/v1/oauth/google/login"}
          ]
        }
      }
    }

Each contribution is namespaced to the extension that produced it; the
registry rejects duplicates per-namespace to keep the contract
deterministic.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from asterion.extensions.errors import RegistryFrozenError


class ContractContributionRegistry:
    """Holds extension-contributed contract fragments keyed by namespace."""

    __slots__ = ("_contribs", "_frozen")

    def __init__(self) -> None:
        self._contribs: dict[str, Any] = {}
        self._frozen = False

    def add(self, namespace: str, fragment: Mapping[str, Any]) -> None:
        """Register a fragment under ``namespace``.

        ``namespace`` should be the extension name (or ``"<extension>.<aspect>"``
        for multi-aspect contributions). Duplicates raise ``ValueError`` —
        an extension that needs to update its fragment must build it
        completely in one call.
        """
        if self._frozen:
            raise RegistryFrozenError(
                "ContractContributionRegistry is frozen — extensions must add "
                "contract fragments during register_contract_contributions()."
            )
        if not isinstance(namespace, str) or not namespace:
            raise ValueError(f"namespace must be a non-empty string, got {namespace!r}")
        if namespace in self._contribs:
            raise ValueError(f"Contract namespace already registered: {namespace!r}")
        self._contribs[namespace] = dict(fragment)

    def freeze(self) -> None:
        self._frozen = True

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    def all(self) -> dict[str, Any]:
        # Defensive copy — callers must not mutate via the returned dict.
        return {k: dict(v) if isinstance(v, dict) else v for k, v in self._contribs.items()}
