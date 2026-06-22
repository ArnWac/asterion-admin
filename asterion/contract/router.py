"""Admin contract API.

Exposes registered ModelAdmin metadata so the UI / API clients can render
forms, list columns, and validate inputs without hitting CRUD endpoints.

Hidden fields, per-admin protected_fields, and globally protected fields are
never emitted. Resource names are validated through the security validator so
malformed paths fall through to 404 rather than reaching the registry.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from asterion.admin.context import AdminContext, require_admin_context
from asterion.contract.service import (
    CONTRACT_VERSION,
    ModelContractMeta,
    build_model_contract,
    compute_field_permissions,
    resolve_model_scope,
)
from asterion.security.validation import (
    InvalidResourceNameError,
    validate_resource_name,
)

router = APIRouter()


@router.get("/_contract")
async def get_full_contract(
    request: Request,
    ctx: AdminContext = Depends(require_admin_context),
) -> dict:
    runtime = request.app.state.asterion
    # Context-aware sidebar filter (Phase A). In multi-tenant mode the full
    # contract — which feeds the sidebar and dashboard — lists only the
    # resources reachable in the current request scope: tenant-scoped models
    # require an active tenant, public models only resolve outside one. This
    # mirrors what the CRUD endpoints accept, so the UI can't offer a link
    # that would 500 with "relation does not exist". Single-tenant apps
    # (no TenantMiddleware, ctx.tenant always None) skip the filter entirely
    # and see every registered resource, exactly as before.
    multi_tenant = runtime.config.enable_multi_tenant
    in_tenant = ctx.tenant is not None
    models: list[dict] = []
    for admin in runtime.registry.all():
        if multi_tenant and (resolve_model_scope(admin) == "tenant") != in_tenant:
            continue
        # Field permissions are policy.field_permission() output for
        # the calling principal — pre-computed here (async) so the
        # sync builder below can stamp the result into FieldMeta.
        field_permissions = await compute_field_permissions(admin, ctx)
        models.append(
            build_model_contract(
                admin,
                registry=runtime.fields,
                permissions=ctx.permissions,
                admin_registry=runtime.registry,
                field_permissions=field_permissions,
            ).model_dump()
        )
    return {
        "contract_version": CONTRACT_VERSION,
        "models": models,
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
    try:
        resource = validate_resource_name(resource)
    except InvalidResourceNameError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Resource '{resource}' is not registered.",
        ) from None
    runtime = request.app.state.asterion
    admin = runtime.registry.get(resource)
    if admin is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Resource '{resource}' is not registered.",
        )
    field_permissions = await compute_field_permissions(admin, ctx)
    return build_model_contract(
        admin,
        registry=runtime.fields,
        permissions=ctx.permissions,
        admin_registry=runtime.registry,
        field_permissions=field_permissions,
    )
