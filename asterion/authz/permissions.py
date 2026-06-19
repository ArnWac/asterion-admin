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
