"""Static guard: forbidden legacy patterns must not reappear in active code.

This test scans every .py file in the asterion package and rejects any
match against the legacy patterns documented in the v1 migration plan.

If you intentionally need one of these strings (e.g. in a docstring), narrow
the regex or add an explicit allowlist below. Do not silence by deleting the
test.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "asterion"


FORBIDDEN_PATTERNS: dict[str, re.Pattern[str]] = {
    "admin_site": re.compile(r"\badmin_site\b"),
    # NOTE: ``AuthProvider`` is intentionally NOT in this list anymore. The
    # name now refers to the neutral ``asterion.providers.base.AuthProvider``
    # Protocol introduced by the v1-providers refactor, which is the
    # opposite of the legacy concrete-class concept this guard used to
    # forbid. Re-add only if a future iteration moves away from the name.
    "PolicyEngine": re.compile(r"\bPolicyEngine\b"),
    "policy_engine module": re.compile(r"\bpolicy_engine\b"),
    # NOTE: ``ExtensionRegistry`` is intentionally NOT in this list anymore.
    # The name now refers to the formal class introduced by the Phase 5
    # extension-system refactor (was a banned legacy term in v0).
    "DashboardRegistry": re.compile(r"\bDashboardRegistry\b"),
    "EventBus": re.compile(r"\bEventBus\b"),
    "AsyncSessionLocal": re.compile(r"\bAsyncSessionLocal\b"),
    "get_admin_db": re.compile(r"\bget_admin_db\b"),
    "asterion.settings": re.compile(r"asterion\.settings\b"),
    # bare Role/RolePermission classes (excluding TenantRole/TenantRolePermission)
    "bare RolePermission class": re.compile(r"(?<!Tenant)\bRolePermission\b"),
    "bare Role import": re.compile(
        r"\bfrom\s+asterion\.models\s+import\s+[^\n]*\bRole\b(?!Permission)"
    ),
    "user_roles association": re.compile(r"\buser_roles\b"),
    "membership_roles assoc": re.compile(r"\bmembership_roles\b"),
}


def _all_python_files() -> list[Path]:
    return [p for p in PACKAGE_ROOT.rglob("*.py") if "__pycache__" not in p.parts]


@pytest.mark.parametrize("label,pattern", list(FORBIDDEN_PATTERNS.items()))
def test_no_forbidden_legacy_patterns(label: str, pattern: re.Pattern[str]):
    offenders: list[str] = []
    for path in _all_python_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                offenders.append(
                    f"{path.relative_to(PACKAGE_ROOT.parent)}:{lineno}: {line.strip()}"
                )

    assert not offenders, (
        f"Forbidden legacy pattern {label!r} reappeared in active code:\n" + "\n".join(offenders)
    )


def test_package_tree_only_contains_v1_subpackages():
    """Active code lives in a fixed set of subpackages. New top-level packages
    indicate scope creep and should be reviewed by a human."""
    allowed = {
        "__init__.py",
        "actions",
        "admin",
        "app.py",
        "audit",
        "auth",
        "authz",
        "builtins",
        "cli",
        "contract",
        "core",
        "crud",
        "db",
        "extensions",
        "fields",
        "i18n",
        "models",
        "notifications",
        "providers",
        "registry",
        "root",
        "schemas",
        "security",
        "storage",
        "tenancy",
        "ui",
        # PEP 561 marker — flat file, not a subpackage
        "py.typed",
    }
    present = {
        p.name
        for p in PACKAGE_ROOT.iterdir()
        if not p.name.startswith("_") and p.name != "__pycache__"
    }
    extras = present - allowed
    assert not extras, f"Unexpected top-level packages in asterion/: {sorted(extras)}"
