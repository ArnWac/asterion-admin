"""JS-side detection contract for the audit diff renderer (Roadmap 5.1b).

Runs ``tests/ui/diff_detection_runner.mjs`` under ``node`` and asserts
it exits 0. The runner imports ``looksLikeAuditDiff`` from the shipped
``diff.js`` and checks both the positive (audit-shaped blobs) and
negative (everything else) cases. Skipped when ``node`` isn't on
PATH so contributors without it see a clean skip, not a hard failure.

The runner doesn't pull in a JS test framework — it's plain
``node:assert`` + dynamic import. Cheap to keep, cheap to read,
no transitive dev dependency.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNNER = _REPO_ROOT / "tests" / "ui" / "diff_detection_runner.mjs"
_DIFF_JS = _REPO_ROOT / "asterion" / "ui" / "static" / "admin" / "diff.js"


def test_diff_js_module_exists():
    """Sanity — the runner can't test anything if the source moved."""
    assert _DIFF_JS.is_file(), f"diff.js missing at {_DIFF_JS}"


def test_looks_like_audit_diff_detection_matches_spec():
    if shutil.which("node") is None:
        pytest.skip("node not on PATH — skipping JS-side detection check")

    result = subprocess.run(
        ["node", str(_RUNNER), str(_DIFF_JS)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"node runner failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.stdout.strip().endswith("ok")
