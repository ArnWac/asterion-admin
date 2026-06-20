from __future__ import annotations

from collections.abc import Collection

from fastapi import HTTPException, status

from asterion.security.validation import (
    InvalidPermissionKeyError,
    validate_action_name,
    validate_permission_key,
    validate_resource_name,
)

DEFAULT_NAMESPACE = "admin"


def permission_key(resource: str, action: str, namespace: str = DEFAULT_NAMESPACE) -> str:
    """Build a concrete required permission key (no wildcards)."""
    resource = validate_resource_name(resource)
    action = validate_action_name(action)
    key = f"{namespace}.{resource}.{action}"
    return validate_permission_key(key)


def _matches_permission(granted: str, required: str) -> bool:
    granted_parts = granted.strip().lower().split(".")
    required_parts = required.strip().lower().split(".")

    if not granted_parts or not required_parts:
        return False

    for index, granted_part in enumerate(granted_parts):
        if granted_part == "*":
            return index == len(granted_parts) - 1 and index <= len(required_parts) - 1

        if index >= len(required_parts):
            return False

        if granted_part != required_parts[index]:
            return False

    return len(granted_parts) == len(required_parts)


def has_permission(
    granted_permissions: Collection[str],
    required_permission: str,
) -> bool:
    try:
        required = validate_permission_key(required_permission)
    except InvalidPermissionKeyError as exc:
        raise ValueError(str(exc)) from exc

    return any(
        _matches_permission(granted, required)
        for granted in granted_permissions
        if granted and granted.strip()
    )


def assert_permission(
    granted_permissions: Collection[str],
    required_permission: str,
) -> None:
    if not has_permission(granted_permissions, required_permission):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing required permission: {required_permission}",
        )


def single_tenant_superadmin_required(ctx) -> bool:
    """Whether admin access in *no-tenant* scope must be superadmin-gated.

    With no tenant context (single-tenant deployments, or root scope) there is
    no tenant-role/permission-key system to gate by, so by default the admin
    surface requires a superadmin — otherwise any authenticated, active account
    could manage everything. Controlled by
    ``CoreAdminConfig.single_tenant_require_superadmin`` (default True); set it
    False to restore the legacy "any authenticated caller" behaviour.

    ``ctx`` is duck-typed (avoids importing :class:`AdminContext` here). The
    flag is read from the request on the context; when no request/config is
    reachable (e.g. a manually-built context in a unit test) it fails **safe**
    to True.
    """
    req = getattr(ctx, "request", None)
    if req is None:
        return True
    runtime = getattr(getattr(req, "app", None), "state", None)
    runtime = getattr(runtime, "asterion", None)
    cfg = getattr(runtime, "config", None)
    return bool(getattr(cfg, "single_tenant_require_superadmin", True))


def require_resource_access(ctx, resource: str, action: str) -> None:
    """Central per-resource authorization gate for the admin endpoints.

    * **Inside a tenant**: require the per-resource permission key
      (``admin.<resource>.<action>``).
    * **No tenant context** (single-tenant deployments / root scope): there is
      no tenant-role system, so require a superadmin **or** an explicitly
      granted permission key. The default permission provider grants
      non-superadmins nothing in single-tenant, so this is effectively
      superadmin-only there — which is the point: a logged-in account is not an
      admin just by existing. Set ``single_tenant_require_superadmin=False`` to
      restore the legacy "any authenticated caller" behaviour.

    ``ctx`` is duck-typed (``tenant``, ``is_superadmin``, ``has_permission``).
    """
    required = permission_key(resource, action)
    if ctx.tenant is None:
        if not single_tenant_superadmin_required(ctx):
            return
        if getattr(ctx, "is_superadmin", False) or ctx.has_permission(required):
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superadmin privileges required.",
        )
    if not ctx.has_permission(required):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing required permission: {required}",
        )
