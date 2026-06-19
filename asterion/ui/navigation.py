"""Minimal NavigationRegistry — extension-contributed UI items.

Phase 5 ships the container; Phase 9 wires it into the UI shell so the
sidebar shows extension-supplied items (gated by the item's permission
through :meth:`asterion.admin.AdminContext.has_permission`).

Each item must be permission-gated. Extensions that contribute a
navigation item without a permission attribute trip a ValueError —
unfiltered admin nav is a security smell.
"""

from __future__ import annotations

from dataclasses import dataclass

from asterion.extensions.errors import RegistryFrozenError


@dataclass(frozen=True, slots=True)
class NavigationItem:
    id: str  #: globally unique, namespaced (e.g. "oauth.identities")
    label: str  #: display text
    path: str  #: UI path (relative to the app)
    permission: str  #: required permission key — UI hides item without it


class NavigationRegistry:
    __slots__ = ("_frozen", "_items", "_seen_ids")

    def __init__(self) -> None:
        self._items: list[NavigationItem] = []
        self._seen_ids: set[str] = set()
        self._frozen = False

    def add_item(
        self,
        *,
        id: str,
        label: str,
        path: str,
        permission: str,
    ) -> None:
        if self._frozen:
            raise RegistryFrozenError(
                "NavigationRegistry is frozen — extensions must add nav items "
                "during register_navigation()."
            )
        if not id or "." not in id:
            raise ValueError(
                f"Navigation item id must be namespaced (e.g. 'oauth.identities'), got {id!r}"
            )
        if id in self._seen_ids:
            raise ValueError(f"Navigation item id already registered: {id!r}")
        if not permission:
            raise ValueError(
                f"Navigation item {id!r} must declare a permission "
                "(unfiltered admin navigation is a security smell)"
            )
        self._items.append(NavigationItem(id=id, label=label, path=path, permission=permission))
        self._seen_ids.add(id)

    def freeze(self) -> None:
        self._frozen = True

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    def all(self) -> tuple[NavigationItem, ...]:
        return tuple(self._items)
