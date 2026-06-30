"""G12 tripwire: test/example fixtures must not contain real personal data.

The framework's own test + example fixtures should only ever use synthetic,
unroutable identifiers. The realistic leak vector is someone pasting a *real*
e-mail address (their own, a customer's) into a fixture — so this test scans the
test and example trees and fails if any e-mail address uses a **real consumer
mail provider** (gmail, web.de, …) or another known real mailbox host.

It deliberately does **not** demand RFC-2606 reserved domains only: the suite
uses short synthetic hosts like ``x.com`` / ``y.com`` that are obviously
non-personal. The denylist targets the high-signal case (a real person's
address) with near-zero false positives. Extend ``_REAL_MAIL_PROVIDERS`` if a
new provider shows up in a leak.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCANNED_DIRS = (PROJECT_ROOT / "tests", PROJECT_ROOT / "examples")

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")

#: Real, routable mailbox hosts. An address at any of these in a fixture almost
#: certainly belongs to a real person — exactly the PII that must not land here.
_REAL_MAIL_PROVIDERS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "ymail.com",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "msn.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "aol.com",
        "proton.me",
        "protonmail.com",
        "gmx.de",
        "gmx.net",
        "web.de",
        "t-online.de",
        "freenet.de",
        "mail.ru",
        "yandex.ru",
        "zoho.com",
        "fastmail.com",
    }
)


#: This module plants a real-looking address on purpose (the denylist sanity
#: check), so it must exclude itself from the scan.
_SELF = Path(__file__).resolve()


def _python_files() -> list[Path]:
    files: list[Path] = []
    for base in SCANNED_DIRS:
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts or path.resolve() == _SELF:
                continue
            files.append(path)
    return files


def test_fixtures_have_no_real_provider_emails():
    offenders: list[str] = []
    for path in _python_files():
        text = path.read_text(encoding="utf-8")
        for match in _EMAIL_RE.finditer(text):
            domain = match.group(1).lower()
            if domain in _REAL_MAIL_PROVIDERS:
                rel = path.relative_to(PROJECT_ROOT)
                offenders.append(f"{rel}: {match.group(0)}")
    assert not offenders, (
        "Real consumer-provider e-mail(s) found in fixtures — use a synthetic "
        "domain (example.com / *.invalid / *.test):\n  " + "\n  ".join(offenders)
    )


def test_scanner_actually_sees_the_tree():
    # Guard against the scan silently matching nothing (e.g. a path refactor):
    # the tripwire is only meaningful if it's actually reading files.
    files = _python_files()
    assert len(files) > 50, f"PII scan covered too few files ({len(files)}) — check SCANNED_DIRS."


def test_denylist_would_catch_a_real_address():
    # Sanity: the matcher + denylist actually flag a planted real address.
    sample = "someone@gmail.com and ok@example.com"
    hits = [d.lower() for d in _EMAIL_RE.findall(sample) if d.lower() in _REAL_MAIL_PROVIDERS]
    assert hits == ["gmail.com"]


@pytest.mark.parametrize("synthetic", ["a@example.com", "b@x.test", "c@acme.invalid"])
def test_synthetic_domains_are_allowed(synthetic):
    domain = _EMAIL_RE.findall(synthetic)[0].lower()
    assert domain not in _REAL_MAIL_PROVIDERS
