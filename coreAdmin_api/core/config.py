"""Typed configuration model for the coreAdmin framework.

Runtime code should consume CoreAdminConfig, not raw environment variables.
Use CoreAdminConfig.from_settings(settings) to build from the env-backed Settings object.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CoreAdminConfig:
    """Central typed configuration for a coreAdmin installation.

    Defaults produce a minimal core installation — optional extensions are off.
    Disabled features do not mount routers, expose metadata, or import heavy deps.

    Example::

        config = CoreAdminConfig(
            enable_builtin_ui=True,
            enable_multi_tenant=False,
            enable_basic_audit=True,
            enable_workflows=False,
            extensions=[JobsExtension()],
        )
    """

    # Core features
    enable_builtin_ui: bool = True
    enable_multi_tenant: bool = False
    enable_basic_audit: bool = True
    enable_workflows: bool = False

    # User-supplied extension instances (loaded in registration order)
    extensions: list[Any] = field(default_factory=list)

    @classmethod
    def from_settings(cls, settings: Any) -> CoreAdminConfig:
        """Build config from the env-backed Settings object."""
        return cls(
            enable_builtin_ui=getattr(settings, "ENABLE_BUILTIN_ADMIN_UI", True),
            enable_multi_tenant=getattr(settings, "MULTI_TENANT", False),
            enable_basic_audit=True,
            enable_workflows=getattr(settings, "ENABLE_WORKFLOWS", False),
        )

    def enabled_extension_names(self) -> list[str]:
        """Return the names of all enabled user-provided extensions."""
        names: list[str] = []
        if self.enable_workflows:
            names.append("workflows")
        for ext in self.extensions:
            name = getattr(ext, "name", None)
            if name:
                names.append(name)
        return names

    def to_safe_dict(self) -> dict:
        """UI-safe representation for admin contract and diagnostics."""
        return {
            "enable_builtin_ui": self.enable_builtin_ui,
            "enable_multi_tenant": self.enable_multi_tenant,
            "enable_basic_audit": self.enable_basic_audit,
            "enable_workflows": self.enable_workflows,
            "enabled_extensions": self.enabled_extension_names(),
        }
