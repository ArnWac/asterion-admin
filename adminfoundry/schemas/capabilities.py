from pydantic import BaseModel


class TenantLocale(BaseModel):
    timezone: str | None = None
    language: str | None = None
    date_format: str | None = None
    date_pattern: str | None = None


class TenantContext(BaseModel):
    id: str
    name: str
    slug: str
    locale: TenantLocale | None = None


class ModelCapabilities(BaseModel):
    model: str
    can_list: bool
    can_create: bool
    can_read: bool
    can_update: bool
    can_delete: bool


class CapabilitiesResponse(BaseModel):
    contract_version: str           # matches the admin contract version
    is_superadmin: bool
    is_impersonating: bool
    impersonated_by: str | None     # superadmin user ID, if impersonating
    models: list[ModelCapabilities]


class AdminContextResponse(BaseModel):
    contract_version: str
    user_id: str
    email: str
    full_name: str | None
    is_superadmin: bool
    is_impersonating: bool
    impersonated_by: str | None
    tenant: TenantContext | None
    # enabled feature flags (additive, always present)
    enabled_features: dict | None = None
