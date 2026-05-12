"""adminfoundry — FastAPI admin framework with built-in UI, auth, and multi-tenancy."""

from adminfoundry.admin.model_admin import ModelAdmin
from adminfoundry.admin.registry import admin_site
from adminfoundry.admin.router import create_coreadmin
from adminfoundry.auth_provider import AuthProvider
from adminfoundry.core.config import CoreAdminConfig
from adminfoundry import signals, webhooks
from adminfoundry.cache import cache
from adminfoundry.storage import storage
from adminfoundry.i18n import t
from adminfoundry.dashboard import DashboardWidget

__version__ = "0.1.0"

__all__ = [
    "create_coreadmin",
    "CoreAdminConfig",
    "ModelAdmin",
    "admin_site",
    "AuthProvider",
    "DashboardWidget",
    "signals",
    "webhooks",
    "cache",
    "storage",
    "t",
    "__version__",
]
