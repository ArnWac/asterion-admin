"""
Regression tests for the library/example boundary.

- The framework package must not auto-import demo admin registrations.
- create_admin must be the public entrypoint (no create_coreadmin).
- Example apps must be importable without crashing.
"""
import sys


def test_framework_does_not_import_example_admin_config():
    """Importing adminfoundry must not pull in examples.default.admin_config."""
    # Clear any cached state from previous tests
    for key in list(sys.modules.keys()):
        if "examples.default.admin_config" in key or "adminfoundry.admin_config" in key:
            del sys.modules[key]

    import adminfoundry  # noqa: F401

    assert "adminfoundry.admin_config" not in sys.modules, (
        "adminfoundry.admin_config leaked into sys.modules — framework must not ship demo registrations"
    )
    assert "examples.default.admin_config" not in sys.modules, (
        "examples.default.admin_config leaked on bare framework import"
    )


def test_create_admin_is_public_entrypoint():
    """create_admin is callable and exported from adminfoundry."""
    from adminfoundry import create_admin
    assert callable(create_admin)


def test_create_coreadmin_is_gone():
    """create_coreadmin must not exist in the public API."""
    import adminfoundry
    assert not hasattr(adminfoundry, "create_coreadmin"), (
        "create_coreadmin should have been removed — use create_admin instead"
    )


def test_basic_single_tenant_example_imports():
    """Smoke import of the basic single-tenant example — run in subprocess to avoid polluting Base.metadata."""
    import subprocess, sys, os
    env = {**os.environ, "DATABASE_URL": "sqlite+aiosqlite:///:memory:"}
    result = subprocess.run(
        [sys.executable, "-c", "import examples.basic_single_tenant.admin_config"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr


def test_basic_multi_tenant_example_imports():
    """Smoke import of the basic multi-tenant example — run in subprocess to avoid polluting Base.metadata."""
    import subprocess, sys, os
    env = {**os.environ, "DATABASE_URL": "sqlite+aiosqlite:///:memory:", "MULTI_TENANT": "true"}
    result = subprocess.run(
        [sys.executable, "-c", "import examples.basic_multi_tenant.admin_config"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr
