"""Renderer capability declaration for the built-in lightweight admin UI.

Clients and tests can query this to discover which features the built-in
renderer supports, and which degrade safely with a fallback state.
"""
from __future__ import annotations

# Renderer identity
RENDERER_ID = "builtin-lightweight"
RENDERER_VERSION = "11.0"

# Explicit capability map — True = supported, False = not yet supported.
# Unsupported features must degrade safely (no hard error in the UI).
SUPPORTED_FEATURES: dict[str, bool] = {
    # Core CRUD flows
    "list": True,
    "detail": True,
    "create": True,
    "update": True,
    "delete": True,
    # List capabilities
    "search": True,
    "ordering": True,
    "pagination": True,
    "field_filters": True,
    # Context and security
    "tenant_context": True,
    "impersonation_indicator": True,
    "readonly_fields": True,
    "protected_field_filtering": True,
    # Advanced flows
    "dangerous_actions": True,
    "bulk_actions": True,
    "audit_log_view": False,
    "export": True,
    "import": True,
    # Relation handling
    "relation_selection": True,
    # Workflow
    "workflow_approval": False,
    # Quality
    "localization_ready": True,
    "accessibility_baseline": True,
    "unsupported_features_degrade_safely": True,
    "preference_persistence": True,
    "impersonation_state_visible": True,
    "validation_field_level_errors": True,
}


def get_support_matrix() -> dict:
    """Return the full renderer support matrix as a plain dict."""
    return {
        "renderer": RENDERER_ID,
        "version": RENDERER_VERSION,
        "supported": SUPPORTED_FEATURES,
    }
