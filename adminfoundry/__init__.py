"""adminfoundry — FastAPI admin framework with built-in UI, auth, and multi-tenancy."""

from adminfoundry.admin.model_admin import ModelAdmin
from adminfoundry.admin.registry import admin_site
from adminfoundry.admin.router import create_admin
from adminfoundry.auth_provider import AuthProvider
from adminfoundry.core.config import CoreAdminConfig
from adminfoundry import signals
from adminfoundry.cache import cache
from adminfoundry.storage import storage
from adminfoundry.i18n import t
from adminfoundry.dashboard import DashboardWidget
from adminfoundry.actions import (
    BulkDeleteAction,
    DeactivateUsersAction,
    ActivateUsersAction,
    DisableTenantAction,
    EnableTenantAction,
)

__version__ = "0.1.0"

__all__ = [
    "create_admin",
    "CoreAdminConfig",
    "ModelAdmin",
    "admin_site",
    "AuthProvider",
    "DashboardWidget",
    "BulkDeleteAction",
    "DeactivateUsersAction",
    "ActivateUsersAction",
    "DisableTenantAction",
    "EnableTenantAction",
    "signals",
    "cache",
    "storage",
    "t",
    "__version__",
]
