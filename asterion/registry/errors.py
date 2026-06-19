class RegistryError(Exception):
    """Base exception for registry errors."""


class ModelAdminConfigurationError(RegistryError):
    """Raised when a ModelAdmin class is invalid."""


class ModelAlreadyRegisteredError(RegistryError):
    """Raised when a model or resource is already registered."""


class ModelNotRegisteredError(RegistryError):
    """Raised when a model or resource is not registered."""
