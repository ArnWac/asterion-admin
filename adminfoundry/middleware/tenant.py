"""Re-export shim — logic lives in adminfoundry.tenancy.*"""
from adminfoundry.tenancy.middleware import TenantMiddleware
from adminfoundry.tenancy.resolver import clear_tenant_cache

__all__ = ["TenantMiddleware", "clear_tenant_cache"]
