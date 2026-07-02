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

#: The platform tier's namespace (ADR-0004). Keys under ``platform.*`` express
#: authority a tenant cannot mint: they are held only by superadmins (mapped in
#: the ``PermissionProvider``) and — from Phase 2 — by platform staff via the
#: public-schema ``PlatformRole`` store. They are never assignable through
#: tenant RBAC, so a tenant ``owner`` (who holds ``admin.*``) can never obtain
#: them. This is what lets a gate distinguish the platform operator from a
#: tenant owner, which ``admin.*`` alone cannot.
PLATFORM_NAMESPACE = "platform"

#: God-mode marker: the grant a full superadmin carries. Gates for
#: platform-operator-only surfaces (impersonation, cross-tenant tooling) require
#: this exact key — a scoped staff grant like ``platform.tenants.read`` does not
#: match it, so staff are kept out of god-mode routes.
PLATFORM_WILDCARD = f"{PLATFORM_NAMESPACE}.*"


def permission_key(resource: str, action: str, namespace: str = DEFAULT_NAMESPACE) -> str:
    """Build a concrete required permission key (no wildcards)."""
    resource = validate_resource_name(resource)
    action = validate_action_name(action)
    key = f"{namespace}.{resource}.{action}"
    return validate_permission_key(key)


def platform_key(resource: str, action: str) -> str:
    """Build a concrete ``platform.<resource>.<action>`` key (ADR-0004)."""
    return permission_key(resource, action, namespace=PLATFORM_NAMESPACE)


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
        # Platform authority (``platform.*``, held by superadmins) or an
        # explicit ``admin.<res>.<action>`` grant clears the no-tenant gate.
        # Checked via keys, not ``is_superadmin`` — one authorization channel
        # (ADR-0004); a superadmin reaches this through the ``platform.*`` grant
        # the PermissionProvider maps for them.
        if ctx.has_permission(PLATFORM_WILDCARD) or ctx.has_permission(required):
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
