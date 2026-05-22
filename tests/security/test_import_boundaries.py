"""AST-based import-boundary check.

Hard architectural invariant from the v1-providers refactor:

* **Core code does NOT import from concrete extensions.**
  Allowed: ``from adminfoundry.extensions import AdminExtension`` (the
  package-level Protocol / DTO surface).
  Forbidden: ``from adminfoundry.extensions.import_export...``,
  ``from adminfoundry.extensions.auth_oauth...``, etc.

* Extensions may import freely from Core. That direction is what makes
  optional features actually optional.

The check parses every .py file under ``adminfoundry/`` (excluding the
``extensions/`` subtree itself) and asserts each ``import`` /
``from ... import ...`` statement does not target an extension submodule.

If you ever need to break this rule (legitimately — e.g. for a
framework-shipped extension that becomes core), update the allowlist
below WITH a comment explaining the decision.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "adminfoundry"
EXTENSIONS_ROOT = PACKAGE_ROOT / "extensions"

#: Submodule names directly under ``adminfoundry.extensions/`` count as
#: concrete extensions. Discovered at test time so new extensions are
#: covered automatically.
def _concrete_extension_names() -> set[str]:
    return {
        p.name
        for p in EXTENSIONS_ROOT.iterdir()
        if p.is_dir() and not p.name.startswith("_") and p.name != "__pycache__"
    }


#: Allowlist of intentional exceptions. None for now.
ALLOWED_EXCEPTIONS: set[tuple[str, str]] = set()


def _iter_core_python_files() -> list[Path]:
    """Every .py file under adminfoundry/ that is NOT inside extensions/."""
    files: list[Path] = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        # Skip the entire extensions/ subtree — those files are extension code,
        # not core code, and may legitimately import other extensions.
        try:
            path.relative_to(EXTENSIONS_ROOT)
        except ValueError:
            files.append(path)
    return files


def _imports_in(path: Path) -> list[tuple[int, str]]:
    """Return (lineno, dotted-module-name) for each import statement."""
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # Relative imports don't reach across the package boundary
                # we're policing.
                continue
            if node.module:
                out.append((node.lineno, node.module))
    return out


def _is_forbidden(import_target: str, concrete: set[str]) -> bool:
    """``adminfoundry.extensions.<name>(.something)*`` where ``<name>`` is
    a concrete extension package — forbidden in core."""
    parts = import_target.split(".")
    if len(parts) < 3:
        return False
    if parts[0] != "adminfoundry" or parts[1] != "extensions":
        return False
    return parts[2] in concrete


def test_core_does_not_import_concrete_extensions():
    concrete = _concrete_extension_names()
    if not concrete:
        pytest.skip("no concrete extensions installed — nothing to police")

    offenders: list[str] = []
    for path in _iter_core_python_files():
        rel = path.relative_to(PACKAGE_ROOT.parent)
        for lineno, target in _imports_in(path):
            if not _is_forbidden(target, concrete):
                continue
            if (str(rel), target) in ALLOWED_EXCEPTIONS:
                continue
            offenders.append(f"{rel}:{lineno}: imports {target!r}")

    assert not offenders, (
        "Core code must not import concrete extensions. Offenders:\n"
        + "\n".join(offenders)
        + "\n\nIf this is intentional, add to ALLOWED_EXCEPTIONS in this file "
        "with a comment explaining why."
    )


def test_extensions_can_import_core():
    """Sanity check on the inverse direction — proves the test isn't accidentally
    forbidding both ways.

    A concrete extension should be able to import core freely; this is the
    whole point of Phase 5/6. We don't assert any specific import here, just
    that the import-detection works as expected for the allowed direction.
    """
    if not (EXTENSIONS_ROOT / "import_export").exists():
        pytest.skip("import_export extension not present")
    imports = _imports_in(EXTENSIONS_ROOT / "import_export" / "router.py")
    # The extension imports from a bunch of core packages — confirm at least one.
    core_imports = [
        target
        for _, target in imports
        if target.startswith("adminfoundry.") and not target.startswith("adminfoundry.extensions")
    ]
    assert core_imports, "import_export extension is expected to import from core"


def test_no_import_of_extensions_in_router_layer():
    """Specific tighter check: the routers (which run inside requests) must
    not have any hard dependency on concrete extension internals — even if
    Python's import system would tolerate it."""
    concrete = _concrete_extension_names()
    if not concrete:
        pytest.skip("no concrete extensions installed")

    router_paths = list((PACKAGE_ROOT / "crud").rglob("*.py")) + \
                   list((PACKAGE_ROOT / "contract").rglob("*.py")) + \
                   list((PACKAGE_ROOT / "actions").rglob("*.py")) + \
                   list((PACKAGE_ROOT / "auth").rglob("*.py")) + \
                   list((PACKAGE_ROOT / "root").rglob("*.py")) + \
                   list((PACKAGE_ROOT / "ui").rglob("*.py"))

    offenders: list[str] = []
    for path in router_paths:
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(PACKAGE_ROOT.parent)
        for lineno, target in _imports_in(path):
            if _is_forbidden(target, concrete):
                offenders.append(f"{rel}:{lineno}: imports {target!r}")

    assert not offenders, "Router layer must not import concrete extensions:\n" + "\n".join(offenders)
