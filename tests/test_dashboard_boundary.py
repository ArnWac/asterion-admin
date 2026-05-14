"""Guard the Dashboard / Observability boundary.

Core dashboard provides infrastructure + generic widgets only.
Concrete metric widgets are contributed by ObservabilityExtension via
ExtensionBase.get_dashboard_widgets(). Disabled extensions contribute nothing.

The "live app" tests run in a fresh subprocess because create_admin mutates
module-level state (_admin_config, _extension_widgets) shared with the
multi-tenant example app loaded by conftest.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

from adminfoundry.dashboard import (
    DEFAULT_WIDGETS,
    DashboardWidget,
    ModelCountsWidget,
)


_DASHBOARD_SRC = Path(__file__).resolve().parents[1] / "adminfoundry" / "dashboard.py"


def _run(script: str) -> None:
    subprocess.check_call([sys.executable, "-c", textwrap.dedent(script)])


# ---------------------------------------------------------------------------
# Static / in-process checks
# ---------------------------------------------------------------------------

def test_core_dashboard_imports_no_extension_observability():
    """Core dashboard module must not reference adminfoundry.extensions.observability."""
    src = _DASHBOARD_SRC.read_text(encoding="utf-8")
    assert "extensions.observability" not in src
    assert "extensions/observability" not in src


def test_default_widgets_contains_only_core_widgets():
    """DEFAULT_WIDGETS is the core generic widget list — ModelCountsWidget only."""
    assert len(DEFAULT_WIDGETS) == 1
    assert isinstance(DEFAULT_WIDGETS[0], ModelCountsWidget)


def test_observability_extension_contributes_admin_metrics_widget():
    """ObservabilityExtension contributes AdminMetricsWidget via get_dashboard_widgets()."""
    from adminfoundry.extensions.observability import (
        ObservabilityExtension,
        AdminMetricsWidget,
    )

    widgets = ObservabilityExtension().get_dashboard_widgets()
    assert any(isinstance(w, AdminMetricsWidget) for w in widgets)
    assert all(isinstance(w, DashboardWidget) for w in widgets)


def test_observability_metrics_importable():
    """The observability counter store must be importable from the extension namespace."""
    from adminfoundry.extensions.observability.admin_metrics import get_snapshot
    snap = get_snapshot()
    assert "request_count" in snap
    assert "audit_write_failures" in snap


def test_runtime_metrics_core_module_does_not_exist():
    """runtime_metrics must not exist as a core module — metrics belong to observability."""
    import importlib
    import pytest as _pytest

    with _pytest.raises(ImportError):
        importlib.import_module("adminfoundry.runtime_metrics")


def test_old_middleware_tenant_shim_is_removed():
    """No shim: adminfoundry.middleware.tenant must not exist as a re-export."""
    import importlib
    import pytest as _pytest

    with _pytest.raises(ImportError):
        importlib.import_module("adminfoundry.middleware.tenant")


# ---------------------------------------------------------------------------
# Subprocess-isolated live-app checks (avoid mutating shared module state)
# ---------------------------------------------------------------------------

def test_observability_widgets_absent_when_extension_disabled():
    """With extensions=[], no widget with id=='admin_metrics' is registered."""
    _run("""
        from adminfoundry import create_admin, CoreAdminConfig
        create_admin(config=CoreAdminConfig(extensions=[]), title="boundary-test")
        from adminfoundry.admin import router as r
        ids = [getattr(w, 'id', None) for w in r._extension_widgets]
        assert 'admin_metrics' not in ids, f"unexpected widget ids: {ids}"
    """)


def test_observability_widgets_present_when_extension_enabled():
    """With ObservabilityExtension() registered, admin_metrics widget is appended."""
    _run("""
        from adminfoundry import create_admin, CoreAdminConfig
        from adminfoundry.extensions.observability import ObservabilityExtension
        create_admin(
            config=CoreAdminConfig(extensions=[ObservabilityExtension()]),
            title="boundary-test",
        )
        from adminfoundry.admin import router as r
        ids = [getattr(w, 'id', None) for w in r._extension_widgets]
        assert 'admin_metrics' in ids, f"missing admin_metrics in: {ids}"
    """)


def test_user_dashboard_widgets_replace_defaults():
    """User widgets in CoreAdminConfig.dashboard_widgets replace DEFAULT_WIDGETS."""
    _run("""
        from adminfoundry import create_admin, CoreAdminConfig
        from adminfoundry.dashboard import DashboardWidget

        class _MyWidget(DashboardWidget):
            id = "my_widget"
            title = "Custom"

        widget = _MyWidget()
        create_admin(
            config=CoreAdminConfig(dashboard_widgets=[widget], extensions=[]),
            title="boundary-test",
        )
        from adminfoundry.admin import router as r
        assert r._admin_config.dashboard_widgets == [widget]
        # Extension widgets are appended; here extensions=[] so nothing is added.
        assert r._extension_widgets == []
    """)
