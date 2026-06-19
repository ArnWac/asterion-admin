from asterion.extensions.errors import RegistryFrozenError
from asterion.registry.admin import ModelAdmin
from asterion.security.validation import validate_resource_name


class AdminRegistry:
    """Holds the registered :class:`ModelAdmin` instances per app.

    Mirrors the freeze semantics of the other framework registries
    (:class:`PermissionRegistry`, :class:`ExtensionRegistry`,
    :class:`ProtectedFieldRegistry`): once :func:`create_admin` finishes
    setup, the registry is frozen and further ``register`` calls raise
    :class:`RegistryFrozenError`. This prevents request-time mutations
    that would silently invalidate cached contracts / route tables.

    The freeze gate is intentionally only on writes — reads
    (``get``, ``all``, ``is_registered``, ``metadata``) stay available.
    """

    def __init__(self) -> None:
        self._registry: dict[str, ModelAdmin] = {}
        self._frozen: bool = False

    def register(self, admin: ModelAdmin | type[ModelAdmin]) -> None:
        if self._frozen:
            raise RegistryFrozenError(
                "AdminRegistry is frozen — register ModelAdmins during the "
                "`register=` callback passed to create_admin(), not after "
                "the app has finished starting up."
            )
        if isinstance(admin, type):
            admin = admin()
        key = validate_resource_name(admin.model_name)
        self._registry[key] = admin

    def freeze(self) -> None:
        """Make the registry immutable. Called by ``create_admin``
        after the user-supplied ``register=`` callback + the extension
        setup phase have run. Safe to call multiple times."""
        self._frozen = True

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    def is_registered(self, model) -> bool:
        return getattr(model, "__tablename__", None) in self._registry

    def get(self, model_name: str) -> ModelAdmin | None:
        return self._registry.get(model_name)

    def all(self) -> list[ModelAdmin]:
        return list(self._registry.values())

    def model_names(self) -> list[str]:
        return list(self._registry.keys())

    def metadata(self) -> list[dict]:
        return [
            {
                "model": admin.model_name,
                "list_display": admin.list_display,
                "search_fields": admin.search_fields,
                "ordering": admin.ordering,
                "readonly_fields": admin.readonly_fields,
            }
            for admin in self._registry.values()
        ]
