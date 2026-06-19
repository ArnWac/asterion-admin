"""Root admin router — superadmin-only endpoints.

These routes never go through tenant middleware / TenantAuthContext, and
``require_superadmin`` rejects impersonation tokens, so root operations
cannot be performed by an impersonated session.
"""

from __future__ import annotations

from fastapi import APIRouter

from asterion.root.impersonation import router as _impersonation_router
from asterion.root.tenants import router as _tenants_router
from asterion.root.users import router as _users_router

router = APIRouter()
router.include_router(_impersonation_router)
router.include_router(_users_router)
router.include_router(_tenants_router)
