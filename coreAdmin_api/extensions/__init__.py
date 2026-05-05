"""coreAdmin extension architecture.

Extensions add optional capabilities (jobs, import/export, workflows, etc.)
without bloating the core package.  Each extension declares routers, models,
admin registrations, capability metadata, and startup/health checks.

Usage::

    from coreAdmin_api.extensions.jobs import JobsExtension

    config = CoreAdminConfig(extensions=[JobsExtension()])
"""
from __future__ import annotations

from abc import ABC
from typing import Any


class ExtensionBase(ABC):
    """Base class for all coreAdmin extensions.

    Subclass this and override the methods you need.  All methods have safe
    no-op defaults so extensions can declare only what they provide.
    """

    # Extension identity
    name: str = ""
    version: str = "0.1.0"
    # tier: "core" | "commercial" | "enterprise" | "experimental" | "parked"
    tier: str = "core"
    # If False, startup_check() failure halts the app
    is_optional: bool = True

    # ------------------------------------------------------------------
    # Contribution hooks
    # ------------------------------------------------------------------

    def get_routers(self) -> list:
        """Return FastAPI APIRouter instances to mount."""
        return []

    def get_models(self) -> list:
        """Return SQLAlchemy model classes to include in migration metadata."""
        return []

    def get_admin_registrations(self) -> list:
        """Return ModelAdmin instances to register in admin_site."""
        return []

    def get_capabilities(self) -> dict:
        """Return capability flags exposed through /admin/capabilities."""
        return {}

    def get_settings_schema(self) -> type | None:
        """Return a Pydantic model class for extension-specific settings, or None."""
        return None

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def startup_check(self) -> None:
        """Raise RuntimeError if the extension cannot start.

        Called during app startup (or coreadmin extensions check).
        If is_optional is False and this raises, the app refuses to start.
        """

    def health_check(self) -> dict:
        """Return a health status dict — exposed through doctor / diagnostics."""
        return {
            "status": "ok",
            "extension": self.name,
            "version": self.version,
            "tier": self.tier,
        }

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, tier={self.tier!r})"


class ExtensionRegistry:
    """Deterministic, ordered registry for loaded extensions.

    Extensions are stored in registration order.  Duplicate names are rejected
    so loading order remains stable across restarts.
    """

    def __init__(self) -> None:
        self._extensions: list[ExtensionBase] = []

    def register(self, ext: ExtensionBase) -> None:
        if not ext.name:
            raise ValueError(f"Extension {ext!r} must declare a non-empty name")
        if any(e.name == ext.name for e in self._extensions):
            raise ValueError(f"Extension '{ext.name}' is already registered")
        self._extensions.append(ext)

    def all(self) -> list[ExtensionBase]:
        return list(self._extensions)

    def get(self, name: str) -> ExtensionBase | None:
        for ext in self._extensions:
            if ext.name == name:
                return ext
        return None

    def names(self) -> list[str]:
        return [ext.name for ext in self._extensions]

    def run_startup_checks(self) -> None:
        """Run startup_check() on all registered extensions.

        Required extensions (is_optional=False) raise; optional ones are skipped.
        """
        for ext in self._extensions:
            try:
                ext.startup_check()
            except RuntimeError:
                if not ext.is_optional:
                    raise

    def health_summary(self) -> list[dict]:
        return [ext.health_check() for ext in self._extensions]

    def metadata(self) -> list[dict]:
        """UI-safe metadata for all registered extensions."""
        return [
            {
                "name": ext.name,
                "version": ext.version,
                "tier": ext.tier,
                "is_optional": ext.is_optional,
            }
            for ext in self._extensions
        ]


# Module-level singleton — populated by main.py during app startup
extension_registry = ExtensionRegistry()

__all__ = ["ExtensionBase", "ExtensionRegistry", "extension_registry"]
