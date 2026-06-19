"""ExtensionRegistry — the ordered, name-unique store of configured extensions.

Lives on :class:`AdminRuntime.extensions`. Built by
``create_admin()`` from the ``extensions=`` constructor argument.
Iteration order preserves registration order, which lifecycle hooks
rely on (startup in order, shutdown in reverse).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from asterion.extensions.base import AdminExtension
from asterion.extensions.errors import DuplicateExtensionError, RegistryFrozenError


class ExtensionRegistry:
    """Ordered list of :class:`AdminExtension` instances, indexed by name."""

    __slots__ = ("_by_name", "_frozen", "_order")

    def __init__(self) -> None:
        self._by_name: dict[str, AdminExtension] = {}
        self._order: list[str] = []
        self._frozen = False

    def register(self, extension: AdminExtension) -> None:
        if self._frozen:
            raise RegistryFrozenError(
                "ExtensionRegistry is frozen — register extensions via "
                "create_admin(extensions=[...]) before the app starts serving."
            )
        if not isinstance(extension, AdminExtension):
            raise TypeError(f"Expected an AdminExtension instance, got {type(extension).__name__}")
        name = getattr(extension, "name", "")
        if not name or not isinstance(name, str):
            raise ValueError(
                f"Extension {type(extension).__name__} must declare a non-empty 'name' class attribute"
            )
        if name in self._by_name:
            raise DuplicateExtensionError(
                f"Extension name {name!r} is already registered "
                f"(existing: {type(self._by_name[name]).__name__}, "
                f"new: {type(extension).__name__})"
            )
        self._by_name[name] = extension
        self._order.append(name)

    def register_all(self, extensions: Iterable[AdminExtension]) -> None:
        for ext in extensions:
            self.register(ext)

    def freeze(self) -> None:
        self._frozen = True

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    def get(self, name: str) -> AdminExtension | None:
        return self._by_name.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(self._order)

    def all(self) -> tuple[AdminExtension, ...]:
        return tuple(self._by_name[n] for n in self._order)

    def __iter__(self) -> Iterator[AdminExtension]:
        return iter(self.all())

    def __len__(self) -> int:
        return len(self._order)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._by_name
