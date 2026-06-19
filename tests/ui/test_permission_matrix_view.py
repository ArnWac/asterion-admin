"""UI surface for the permission matrix (Roadmap 5.2b).

Two angles:
* the static-asset mount actually serves the new ``permission_matrix.js``
  module, with its named exports intact;
* the JS-side ``diffAssignments`` helper behaves to spec — verified
  by a Node runner that pytest invokes via subprocess and skips
  cleanly when node isn't installed.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNNER = _REPO_ROOT / "tests" / "ui" / "permission_matrix_diff_runner.mjs"
_VIEW_JS = _REPO_ROOT / "asterion" / "ui" / "static" / "admin" / "views" / "permission_matrix.js"


def test_view_module_exists():
    assert _VIEW_JS.is_file(), f"permission_matrix.js missing at {_VIEW_JS}"


def test_view_exports_diff_assignments_and_mount():
    """Pin the named exports the SPA and tests both depend on."""
    body = _VIEW_JS.read_text(encoding="utf-8")
    assert "export function diffAssignments" in body
    assert "export async function mountPermissionMatrix" in body


def test_diff_assignments_matches_spec():
    if shutil.which("node") is None:
        pytest.skip("node not on PATH — skipping JS-side diff check")

    result = subprocess.run(
        ["node", str(_RUNNER), str(_VIEW_JS)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"node runner failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.stdout.strip().endswith("ok")
