from adminfoundry.admin.model_admin import ModelAdmin


class Registry:
    def __init__(self) -> None:
        self._registry: dict[str, ModelAdmin] = {}

    def register(self, admin: ModelAdmin) -> None:
        key = admin.model_name
        self._registry[key] = admin

    def get(self, model_name: str) -> ModelAdmin | None:
        return self._registry.get(model_name)

    def all(self) -> list[ModelAdmin]:
        return list(self._registry.values())

    def model_names(self) -> list[str]:
        return list(self._registry.keys())

    def metadata(self) -> list[dict]:
        """Return public registry metadata — never exposes protected internals."""
        result = []
        for admin in self._registry.values():
            result.append({
                "model": admin.model_name,
                "list_display": admin.list_display,
                "search_fields": admin.search_fields,
                "filter_fields": admin.filter_fields,
                "ordering": admin.ordering,
                "readonly_fields": admin.readonly_fields,
                # never expose protected_fields or GLOBALLY_PROTECTED in metadata
            })
        return result


admin_site = Registry()
