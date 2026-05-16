"""Per-app runtime state.

`AdminRuntime` replaces the previous module-level globals (`_admin_config`,
`_extension_widgets`, mutated `settings.*`) with a single object attached to
`app.state.adminfoundry`. Each `create_admin()` call constructs its own runtime,
so multiple apps in the same process never share registries or config.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI
    from adminfoundry.auth_provider import AuthProvider
    from adminfoundry.admin.dashboard.registry import DashboardRegistry
    from adminfoundry.cache import CacheBackend
    from adminfoundry.core.config import CoreAdminConfig
    from adminfoundry.core.events import EventBus
    from adminfoundry.extensions import ExtensionRegistry
    from adminfoundry.storage import StorageBackend


@dataclass
class AdminRuntime:
    """Per-app state container. Attached to `app.state.adminfoundry`."""

    config: "CoreAdminConfig"
    auth_provider: "AuthProvider"
    extension_registry: "ExtensionRegistry"
    dashboard_registry: "DashboardRegistry"
    event_bus: "EventBus"
    cache: "CacheBackend | None" = None
    storage: "StorageBackend | None" = None


def get_runtime(app: "FastAPI") -> AdminRuntime:
    return app.state.adminfoundry
