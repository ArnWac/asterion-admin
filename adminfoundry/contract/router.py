"""Admin contract API.

Exposes registered ModelAdmin metadata so the UI / API clients can render
forms, list columns, and validate inputs without hitting CRUD endpoints.

Hidden fields, per-admin protected_fields, and globally protected fields are
never emitted. Resource names are validated through the security validator so
malformed paths fall through to 404 rather than reaching the registry.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from adminfoundry.admin.context import AdminContext, require_admin_context
from adminfoundry.contract.service import (
    CONTRACT_VERSION,
    ModelContractMeta,
    build_model_contract,
)
from adminfoundry.security.validation import (
    InvalidResourceNameError,
    validate_resource_name,
)

router = APIRouter()


@router.get("/_contract")
async def get_full_contract(
    request: Request,
    ctx: AdminContext = Depends(require_admin_context),
) -> dict:
    del ctx  # auth gate only — contract content is identical for every authenticated user
    runtime = request.app.state.adminfoundry
    return {
        "contract_version": CONTRACT_VERSION,
        "models": [build_model_contract(admin).model_dump() for admin in runtime.registry.all()],
        # Extension contributions land under a namespaced top-level key.
        # Each extension owns its namespace (typically the extension name);
        # the UI / API clients iterate over this dict to discover features
        # the framework itself doesn't know about (OAuth providers, etc.).
        "extensions": runtime.contract_contributions.all(),
    }


@router.get("/_contract/{resource}", response_model=ModelContractMeta)
async def get_model_contract(
    resource: str,
    request: Request,
    ctx: AdminContext = Depends(require_admin_context),
) -> ModelContractMeta:
    del ctx  # auth gate only
    try:
        resource = validate_resource_name(resource)
    except InvalidResourceNameError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Resource '{resource}' is not registered.",
        ) from None
    runtime = request.app.state.adminfoundry
    admin = runtime.registry.get(resource)
    if admin is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Resource '{resource}' is not registered.",
        )
    return build_model_contract(admin)
