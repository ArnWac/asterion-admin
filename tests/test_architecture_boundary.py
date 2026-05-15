"""Architecture boundary tests — enforce core/extension import separation.

Core and admin modules must not import directly from concrete extensions.
Extensions integrate only through ExtensionBase hooks (get_routers, get_dashboard_widgets, etc.).
"""
from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1] / "adminfoundry"

# Modules in core/admin that must not reference concrete extensions directly.
_CORE_MODULES = [
    _ROOT / "admin" / "router.py",
    _ROOT / "admin" / "_helpers.py",
    _ROOT / "admin" / "routes" / "contract.py",
    _ROOT / "admin" / "routes" / "dashboard.py",
    _ROOT / "admin" / "routes" / "profile.py",
    _ROOT / "admin" / "routes" / "preferences.py",
    _ROOT / "admin" / "routes" / "permissions.py",
    _ROOT / "admin" / "routes" / "crud.py",
    _ROOT / "admin" / "dashboard" / "widget.py",
    _ROOT / "admin" / "dashboard" / "builtins.py",
    _ROOT / "admin" / "dashboard" / "registry.py",
    _ROOT / "dashboard.py",
]

# Extension sub-packages that core must not import from directly.
_FORBIDDEN_PREFIXES = [
    "adminfoundry.extensions.observability",
    "adminfoundry.extensions.jobs",
    "adminfoundry.extensions.workflows",
    "adminfoundry.extensions.webhooks",
    "adminfoundry.extensions.import_export",
]


def _collect_imports(path: Path) -> list[str]:
    """Return all module strings imported (statically) in a Python source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return imports


def test_admin_modules_do_not_import_concrete_extensions():
    """Core/admin route modules must not import from concrete extension packages."""
    violations: list[str] = []
    for module_path in _CORE_MODULES:
        if not module_path.exists():
            continue
        for imp in _collect_imports(module_path):
            for forbidden in _FORBIDDEN_PREFIXES:
                if imp == forbidden or imp.startswith(forbidden + "."):
                    violations.append(f"{module_path.relative_to(_ROOT.parent)}: imports {imp!r}")

    assert not violations, "Core/admin boundary violations found:\n" + "\n".join(violations)


def test_extension_base_is_importable_from_core():
    """ExtensionBase must be importable from adminfoundry.extensions (the allowed integration point)."""
    from adminfoundry.extensions import ExtensionBase
    assert hasattr(ExtensionBase, "get_routers")
    assert hasattr(ExtensionBase, "get_dashboard_widgets")


def test_dashboard_widget_base_importable_from_canonical_and_compat_paths():
    """DashboardWidget must be importable from both canonical and compat paths."""
    from adminfoundry.admin.dashboard import DashboardWidget as CanonicalWidget
    from adminfoundry.dashboard import DashboardWidget as CompatWidget
    assert CanonicalWidget is CompatWidget


def test_dashboard_registry_singleton_accessible():
    """dashboard_registry singleton must be accessible from the canonical path."""
    from adminfoundry.admin.dashboard.registry import dashboard_registry, DashboardRegistry
    assert isinstance(dashboard_registry, DashboardRegistry)


def test_admin_router_still_exposes_compat_helpers():
    """adminfoundry.admin.router must still export helper names for backward compat."""
    import adminfoundry.admin.router as r
    assert callable(r._check_model_access)
    assert callable(r._tenant_filter)
    assert callable(r._enforce_method_caps)
