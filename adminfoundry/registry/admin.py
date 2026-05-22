from __future__ import annotations

from collections.abc import Callable
from typing import Any

from adminfoundry.security.protected_fields import (
    DEFAULT_PROTECTED_FIELDS,
    get_registry,
)

#: Backward-compatible alias for the default seed. New code should call
#: :func:`adminfoundry.security.protected_fields.get_registry` directly
#: (the registry may have extension-contributed fields beyond this set).
GLOBALLY_PROTECTED: frozenset[str] = DEFAULT_PROTECTED_FIELDS

AUTO_FIELDS: frozenset[str] = frozenset({"id", "created_at", "updated_at"})


class ModelAdmin:
    """MVP admin configuration for a SQLAlchemy model.

    Example::

        class UserAdmin(ModelAdmin):
            model = User
            list_display = ["email", "full_name", "is_active"]
            search_fields = ["email", "full_name"]
            ordering = ["email"]
            readonly_fields = ["id", "created_at", "updated_at"]
    """

    model: type

    label: str | None = None
    label_plural: str | None = None
    description: str | None = None

    list_display: list[str] = []
    search_fields: list[str] = []
    ordering: list[str] = []

    readonly_fields: list[str] = []
    protected_fields: list[str] = []

    actions: list[Any] = []

    calculated_fields: dict[str, Callable[[Any], Any]] = {}

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        for attr in (
            "list_display",
            "search_fields",
            "ordering",
            "readonly_fields",
            "protected_fields",
            "actions",
        ):
            if attr not in cls.__dict__:
                setattr(cls, attr, [])
        if "calculated_fields" not in cls.__dict__:
            cls.calculated_fields = {}

    @property
    def all_protected(self) -> frozenset[str]:
        """Combined set of protected field names for this admin.

        Reads from the live :class:`ProtectedFieldRegistry` (so any
        extension-contributed fields are included) merged with this
        admin's own ``protected_fields``.
        """
        return get_registry().as_frozenset() | frozenset(self.protected_fields)

    @property
    def model_name(self) -> str:
        return self.model.__tablename__

    @property
    def display_label(self) -> str:
        return self.label or self.model.__name__

    @property
    def display_label_plural(self) -> str:
        return self.label_plural or f"{self.display_label}s"
