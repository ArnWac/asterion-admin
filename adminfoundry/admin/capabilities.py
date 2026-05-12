"""Builds capability metadata for the current user and context."""
from __future__ import annotations
from adminfoundry.admin.contract import CONTRACT_VERSION
from adminfoundry.admin.registry import Registry
from adminfoundry.authz.policy_engine import policy_engine
from adminfoundry.schemas.capabilities import (
    AdminContextResponse,
    CapabilitiesResponse,
    ModelCapabilities,
    TenantContext,
    TenantLocale,
)


def _model_caps(
    model_name: str,
    user,
    token_payload: dict,
    registry: Registry,
    all_db_caps: dict | None = None,
    in_tenant_context: bool = False,
) -> ModelCapabilities:
    model_admin = registry.get(model_name)
    if model_admin is None:
        can_all = policy_engine._privileged(user, token_payload)
        return ModelCapabilities(
            model=model_name, can_list=can_all, can_create=can_all,
            can_read=can_all, can_update=can_all, can_delete=can_all,
        )
    db_caps = all_db_caps.get(model_name) if all_db_caps else None
    caps = policy_engine.effective_model_caps(
        user, model_admin, token_payload, db_caps=db_caps, in_tenant_context=in_tenant_context
    )
    return ModelCapabilities(model=model_name, **caps)


def build_capabilities(
    user, token_payload: dict, registry: Registry, all_db_caps: dict | None = None,
    in_tenant_context: bool = False,
) -> CapabilitiesResponse:
    is_impersonating = bool(token_payload.get("impersonated_by"))

    return CapabilitiesResponse(
        contract_version=CONTRACT_VERSION,
        is_superadmin=user.is_superadmin,
        is_impersonating=is_impersonating,
        impersonated_by=token_payload.get("impersonated_by"),
        models=[
            _model_caps(name, user, token_payload, registry, all_db_caps, in_tenant_context)
            for name in registry.model_names()
        ],
    )


def build_admin_context(user, token_payload: dict, request) -> AdminContextResponse:
    is_impersonating = bool(token_payload.get("impersonated_by"))
    tenant = getattr(request.state, "tenant", None)

    from adminfoundry.admin.router import _admin_config
    enabled_features = _admin_config.to_safe_dict() if _admin_config is not None else None

    return AdminContextResponse(
        contract_version=CONTRACT_VERSION,
        user_id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        is_superadmin=user.is_superadmin,
        is_impersonating=is_impersonating,
        impersonated_by=token_payload.get("impersonated_by"),
        tenant=TenantContext(
            id=str(tenant.id),
            name=tenant.name,
            slug=tenant.slug,
            locale=TenantLocale(
                timezone=getattr(tenant, "timezone", None),
                language=getattr(tenant, "language", None),
                date_format=getattr(tenant, "date_format", None),
                date_pattern=getattr(tenant, "date_pattern", None),
            ),
        ) if tenant else None,
        enabled_features=enabled_features,
    )
