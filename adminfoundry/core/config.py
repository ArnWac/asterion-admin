"""Typed configuration model for the adminfoundry framework.

Runtime code should consume CoreAdminConfig, not raw environment variables.
Use CoreAdminConfig.from_settings(settings) to build from the env-backed Settings object.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from adminfoundry.auth_provider import AuthProvider


@dataclass
class CoreAdminConfig:
    """Central typed configuration for an adminfoundry installation.

    Defaults produce a minimal core installation — optional extensions are off.
    Disabled features do not mount routers, expose metadata, or import heavy deps.

    Example::

        config = CoreAdminConfig(
            enable_builtin_ui=True,
            enable_multi_tenant=False,
        )
    """

    # Core features
    enable_builtin_ui: bool = True
    enable_multi_tenant: bool = False
    # "header" uses X-Tenant-Slug; "subdomain" extracts from the first hostname segment
    tenant_resolution: str = "header"

    # Locale defaults — applied as the initial value for all users who have
    # not yet saved a personal preference. Users can override in Settings.
    # language: BCP 47 tag, e.g. "en", "de", "fr"
    default_language: str = "en"
    # date_format: "locale" | "iso" | "eu" | "us" | "custom"
    default_date_format: str = "locale"
    default_date_pattern: str = "%Y-%m-%d %H:%M"
    default_show_timezone: bool = False

    # User-supplied extension instances (loaded in registration order)
    extensions: list[Any] = field(default_factory=list)

    # Optional custom auth provider — None uses the built-in JWT provider
    auth_provider: AuthProvider | None = None
    # Optional custom user model — must have id, email, is_active, is_superadmin.
    # adminfoundry validates the model against these requirements at startup.
    user_model: Any | None = None
    # Set False to skip mounting built-in login/logout/refresh routes
    include_auth_routes: bool = True

    # Cache backend URL — None uses in-process memory; "redis://..." uses Redis
    cache_backend: str | None = None

    # Storage backend instance — None uses LocalStorage("uploads")
    storage_backend: Any | None = None

    # Dashboard widgets — None uses DEFAULT_WIDGETS (ModelCounts + AdminMetrics).
    # Provide a list to control exactly which widgets appear. Use
    # `from adminfoundry.dashboard import DEFAULT_WIDGETS` to extend the defaults:
    #   dashboard_widgets=[*DEFAULT_WIDGETS, MyCustomWidget()]
    dashboard_widgets: list[Any] | None = None

    # Extra i18n strings injected into the admin UI — merged on top of built-in strings.
    # Lets app code add/override translations without modifying the package.
    # Structure: {"en": {"my_action_label": "My Action"}, "de": {"my_action_label": "Meine Aktion"}}
    extra_i18n: dict = field(default_factory=dict)

    @classmethod
    def from_pyproject(cls, path: str | None = None) -> "CoreAdminConfig":
        """Build config from [tool.adminfoundry] in pyproject.toml (Python 3.11+ tomllib)."""
        import tomllib
        from pathlib import Path

        toml_path = Path(path) if path else Path("pyproject.toml")
        if not toml_path.exists():
            return cls()
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
        s = data.get("tool", {}).get("adminfoundry", {})
        return cls(
            enable_builtin_ui=s.get("enable_builtin_ui", True),
            enable_multi_tenant=s.get("enable_multi_tenant", False),
            default_language=s.get("default_language", "en"),
            default_date_format=s.get("default_date_format", "locale"),
            default_date_pattern=s.get("default_date_pattern", "%Y-%m-%d %H:%M"),
            default_show_timezone=s.get("default_show_timezone", False),
        )

    @classmethod
    def from_settings(cls, settings: Any) -> CoreAdminConfig:
        """Build config from the env-backed Settings object."""
        return cls(
            enable_builtin_ui=getattr(settings, "ENABLE_BUILTIN_ADMIN_UI", True),
            enable_multi_tenant=getattr(settings, "MULTI_TENANT", False),
            tenant_resolution=getattr(settings, "TENANT_RESOLUTION_STRATEGY", "header"),
            auth_provider=None,
            include_auth_routes=True,
        )

    def enabled_extension_names(self) -> list[str]:
        """Return the names of all enabled user-provided extensions."""
        return [
            name
            for ext in self.extensions
            if (name := getattr(ext, "name", None))
        ]

    def to_safe_dict(self) -> dict:
        """UI-safe representation for admin contract and diagnostics."""
        return {
            "enable_builtin_ui": self.enable_builtin_ui,
            "enable_multi_tenant": self.enable_multi_tenant,
            "tenant_resolution": self.tenant_resolution,
            "enabled_extensions": self.enabled_extension_names(),
        }
