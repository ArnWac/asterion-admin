"""Admin Pages / Plugin Slots (Roadmap 5.6).

A small SPI for mounting custom pages outside the CRUD schema —
e.g. an extension's "Reports" page, a status dashboard, a bulk-
operation wizard. Each registered page contributes:

* a stable ``id`` (URL slug; also the SPA's view key);
* a ``label`` for the sidebar;
* a ``js_module`` URL the SPA dynamically imports when the page is
  visited;
* an optional ``permission`` for nav-level filtering and route gating;
* an optional ``category`` for grouping in the sidebar.

The framework auto-creates a :class:`NavigationItem` for every
registered page that declares a ``permission``, so extensions don't
have to register navigation separately. Extensions register pages from
their :meth:`AdminExtension.register_admin_pages` hook; the registry
freezes during ``create_admin`` setup, before the first request lands.

Why a separate registry instead of reusing NavigationRegistry
-------------------------------------------------------------

Navigation items only carry a path — they don't know how the SPA
should render that path. Plugin pages need the framework to: (a) mount
a UI route, (b) tell the SPA which JS module to load. Trying to thread
"and here is the module" through NavigationItem mixes two concerns;
keeping them separate lets navigation stay app-agnostic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from asterion.extensions.errors import RegistryFrozenError

if TYPE_CHECKING:  # pragma: no cover
    from asterion.ui.navigation import NavigationRegistry


_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")


@dataclass(frozen=True, slots=True)
class AdminPage:
    """One pluggable admin page.

    ``id`` must be URL-safe ASCII (lowercase letters, digits, ``-``,
    ``_``); it doubles as the URL slug under
    ``{admin_ui_path}/{id}`` AND the SPA's view key. ``js_module`` is
    the URL the SPA's dynamic import resolves — typically a path under
    ``{admin_ui_path}/static/...`` for in-package modules or an
    absolute URL for externally-served JS.
    """

    id: str
    label: str
    js_module: str
    permission: str | None = None
    category: str | None = None


class AdminPageRegistry:
    """Holds the registered admin pages.

    Re-registering the same ``id`` raises — pages are addressable so
    accidental shadowing would be a silent bug. Freezing closes the
    registry before the first request; subsequent ``register`` calls
    are a programming error.
    """

    __slots__ = ("_frozen", "_pages")

    def __init__(self) -> None:
        self._pages: dict[str, AdminPage] = {}
        self._frozen = False

    def register(self, page: AdminPage) -> None:
        if self._frozen:
            raise RegistryFrozenError(
                "AdminPageRegistry is frozen — extensions must register "
                "admin pages during register_routes() (or earlier)."
            )
        if not _ID_PATTERN.match(page.id):
            raise ValueError(f"AdminPage id {page.id!r} must match {_ID_PATTERN.pattern}")
        if page.id in self._pages:
            raise ValueError(f"AdminPage id {page.id!r} is already registered.")
        if not page.label:
            raise ValueError(f"AdminPage {page.id!r} must declare a label.")
        if not page.js_module:
            raise ValueError(f"AdminPage {page.id!r} must declare a js_module URL.")
        self._pages[page.id] = page

    def freeze(self) -> None:
        self._frozen = True

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    def get(self, page_id: str) -> AdminPage | None:
        return self._pages.get(page_id)

    def all(self) -> tuple[AdminPage, ...]:
        return tuple(self._pages.values())

    def __contains__(self, page_id: object) -> bool:
        return isinstance(page_id, str) and page_id in self._pages

    def __len__(self) -> int:
        return len(self._pages)


def mirror_pages_into_navigation(
    pages: AdminPageRegistry,
    navigation: NavigationRegistry,
    *,
    ui_path: str,
) -> None:
    """Copy every registered page into the navigation registry.

    Called once during ``create_admin`` after both registries are
    populated and before the navigation registry is frozen. Pages
    without a permission are skipped — the navigation registry
    requires one (unfiltered nav is a security smell), so callers
    that want their page in the sidebar must declare a permission.
    """
    for page in pages.all():
        if not page.permission:
            continue
        navigation.add_item(
            id=f"page.{page.id}",
            label=page.label,
            # Served under a reserved ``_pages/`` prefix so a page slug can
            # never collide with (or be shadowed by) a CRUD ``/{resource}``
            # route — see ``asterion/ui/router.py``.
            path=f"{ui_path}/_pages/{page.id}",
            permission=page.permission,
        )
