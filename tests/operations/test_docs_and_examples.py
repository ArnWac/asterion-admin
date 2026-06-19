"""PR-7 invariants: docs + examples + deployment artifacts.

These tests guard against three classes of regression:

* a doc claiming to live at `docs/<name>.md` actually exists,
* the bundled example imports + registers cleanly against the current
  ModelAdmin API (catches the historical bug where it used dropped
  attrs like ``filter_fields`` / ``fieldsets`` / ``computed_fields``),
* a flagship deployment artifact (``Dockerfile``, ``.env.example``,
  ``docker-compose.yml``) is present at the repo root.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = PROJECT_ROOT / "docs"


# --- docs presence ---


@pytest.mark.parametrize(
    "filename",
    [
        "architecture.md",
        "security.md",
        "tenancy.md",
        "model-admin.md",
        "deployment.md",
    ],
)
def test_required_doc_exists(filename):
    path = DOCS_DIR / filename
    assert path.exists(), f"Missing doc: docs/{filename}"
    assert path.read_text(encoding="utf-8").strip(), f"docs/{filename} is empty"


def test_no_legacy_doc_left():
    """Old roadmap / protected-fields pages were deleted in PR-7."""
    assert not (DOCS_DIR / "protected-fields.md").exists()
    assert not (DOCS_DIR / "roadmap-postgres-tenancy.md").exists()


# --- README ---


def test_readme_exists_and_mentions_v1_api():
    readme = PROJECT_ROOT / "README.md"
    assert readme.exists()
    content = readme.read_text(encoding="utf-8")
    assert "create_admin" in content
    assert "CoreAdminConfig" in content
    assert "ModelAdmin" in content


def test_readme_does_not_reference_dropped_apis():
    """The historical README referenced `admin_site`, `AuthProvider`,
    `asterion.settings` etc. PR-7 rewrites it; this test guards the
    rewrite from drifting back."""
    content = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    # Use word-boundary checks so unrelated occurrences aren't false hits.
    assert not re.search(r"\badmin_site\b", content)
    assert not re.search(r"\bAuthProvider\b", content)
    assert not re.search(r"asterion\.settings\b", content)


# --- deployment artifacts ---


@pytest.mark.parametrize(
    "filename",
    ["Dockerfile", ".env.example", "docker-compose.yml"],
)
def test_deployment_artifact_exists(filename):
    path = PROJECT_ROOT / filename
    assert path.exists(), f"Missing deployment artifact: {filename}"
    assert path.read_text(encoding="utf-8").strip(), f"{filename} is empty"


def test_env_example_lists_required_vars():
    content = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "ASTERION_DATABASE_URL" in content
    assert "ASTERION_SECRET_KEY" in content


# --- example app ---


def test_basic_single_example_admin_config_imports():
    """The example must import without raising — catches stale attrs."""
    mod = importlib.import_module("examples.basic_single.admin_config")
    assert hasattr(mod, "register")
    assert hasattr(mod, "PostAdmin")


def test_basic_single_example_uses_only_supported_attrs():
    """Any ModelAdmin subclass declared in the example must only set
    attributes that ``ModelAdmin`` actually supports. Catches dropped
    attrs like ``filter_fields``, ``fieldsets``, ``computed_fields``."""
    from asterion import ModelAdmin
    from examples.basic_single.admin_config import PostAdmin

    supported = set(vars(ModelAdmin).keys())
    declared = {name for name in vars(PostAdmin).keys() if not name.startswith("_")}
    extras = declared - supported
    # `model` is the only required class attr that ModelAdmin itself
    # declares via class annotation rather than a value, so it shows up
    # in PostAdmin's dict but not in ModelAdmin's vars on older Pythons.
    extras.discard("model")
    assert not extras, (
        f"PostAdmin sets attrs not present on ModelAdmin: {sorted(extras)}. "
        "PR-7 dropped these — update the example."
    )


def test_basic_single_example_registers_against_real_registry():
    """Round-trip: invoking `register(AdminRegistry())` must not raise
    and must produce at least one registered admin."""
    from asterion import AdminRegistry
    from examples.basic_single.admin_config import register

    registry = AdminRegistry()
    register(registry)
    assert registry.all(), "register() left the registry empty"
    names = [a.model_name for a in registry.all()]
    assert "posts" in names


# --- multi_tenant example (Roadmap 1.8) ---


def test_multi_tenant_example_admin_config_imports():
    """Smoke-test the multi_tenant example's admin_config module —
    catches stale attrs the same way the basic_single tests do.
    Without this, a typo in the second example only surfaces when an
    operator actually runs it."""
    mod = importlib.import_module("examples.multi_tenant.admin_config")
    assert hasattr(mod, "register")


def test_multi_tenant_example_uses_only_supported_admin_attrs():
    """Any ModelAdmin subclass in the multi_tenant example must only
    set attrs ModelAdmin supports. Parity with the basic_single
    guard."""
    from asterion import ModelAdmin
    from examples.multi_tenant import admin_config as mod

    supported = set(vars(ModelAdmin).keys())
    for name, obj in vars(mod).items():
        if not isinstance(obj, type) or not issubclass(obj, ModelAdmin):
            continue
        if obj is ModelAdmin:
            continue
        declared = {n for n in vars(obj).keys() if not n.startswith("_")}
        extras = declared - supported
        extras.discard("model")
        assert not extras, f"{name} sets attrs not present on ModelAdmin: {sorted(extras)}."


def test_multi_tenant_example_registers_against_real_registry():
    """Round-trip the multi_tenant ``register`` callback through a
    real AdminRegistry. Pinning this catches the same class of
    breakage that ``test_basic_single_example_registers...`` catches
    for the simpler example."""
    from asterion import AdminRegistry
    from examples.multi_tenant.admin_config import register

    registry = AdminRegistry()
    register(registry)
    assert registry.all(), "multi_tenant register() left the registry empty"
    names = [a.model_name for a in registry.all()]
    # Multi-tenant example registers tenant-local RBAC admins + the
    # business resources from its sample app.
    assert "projects" in names
    assert "tickets" in names
