"""Pluggable password policy (roadmap G21, NIST SP 800-63B).

NIST 800-63B favours **length + a breach check** over composition rules
("at least one uppercase/digit/symbol"), which push users toward predictable
patterns. This module provides:

* :class:`PasswordPolicy` — a Protocol an app can implement to plug its own
  rules (cf. Django's ``AUTH_PASSWORD_VALIDATORS``);
* :class:`DefaultPasswordPolicy` — length (reusing the existing
  :func:`validate_password_strength`) plus an **opt-in** Have I Been Pwned
  breach check;
* :func:`pwned_password_count` — the HIBP range API via **k-anonymity**: only
  the first 5 chars of the SHA-1 hash ever leave the process, never the password
  or its full hash.

The breach check is **off by default** (it makes an external network call) and
fails **open** — if HIBP is unreachable, the password is allowed rather than
blocking every reset on a third-party outage; the failure is logged.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Protocol, runtime_checkable

from asterion.auth.password import validate_password_strength

logger = logging.getLogger("asterion")

_HIBP_RANGE_URL = "https://api.pwnedpasswords.com/range/"


@runtime_checkable
class PasswordPolicy(Protocol):
    """A password acceptance policy. ``validate`` raises ``ValueError`` (with a
    user-safe message) when the password is unacceptable, else returns ``None``.
    Async so a policy may perform I/O (e.g. a breach lookup)."""

    async def validate(self, password: str) -> None: ...


async def pwned_password_count(
    password: str,
    *,
    timeout: float = 3.0,
    client: object | None = None,
) -> int:
    """Return how many times ``password`` appears in the HIBP breach corpus.

    Uses the k-anonymity range API: the SHA-1 is computed locally, only the
    5-char prefix is sent, and the matching suffix is looked up in the response.
    ``0`` means "not found". Pass ``client`` (an httpx.AsyncClient-like object
    with ``async get(url)``) to inject a transport in tests; otherwise a
    short-lived httpx client is created.

    Raises ``RuntimeError`` if httpx is not installed (the breach check is an
    opt-in extra). Network/HTTP errors propagate to the caller, which decides
    whether to fail open.
    """
    # HIBP's range API is defined over SHA-1; this is a breach-corpus lookup,
    # not password storage (that's bcrypt+SHA-256 in password.py).
    digest = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]

    async def _fetch(c: object) -> str:
        resp = await c.get(_HIBP_RANGE_URL + prefix)  # type: ignore[attr-defined]
        resp.raise_for_status()
        return resp.text

    if client is not None:
        body = await _fetch(client)
    else:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - exercised via clear message
            raise RuntimeError(
                "The HIBP breach check needs httpx. Install asterion with an extra "
                "that provides it (e.g. `.[email-resend]`) or disable "
                "password_hibp_check."
            ) from exc
        async with httpx.AsyncClient(timeout=timeout) as c:
            body = await _fetch(c)

    for line in body.splitlines():
        line_suffix, _, count = line.partition(":")
        if line_suffix.strip().upper() == suffix:
            try:
                return int(count.strip())
            except ValueError:
                return 1
    return 0


class DefaultPasswordPolicy:
    """Length check + optional HIBP breach check.

    ``hibp_check`` is opt-in (external call). When on and HIBP is unreachable the
    check is **skipped** (fail-open) with a warning, so a third-party outage
    can't lock everyone out of password resets.
    """

    def __init__(
        self,
        *,
        min_length: int = 8,
        hibp_check: bool = False,
        hibp_timeout: float = 3.0,
    ) -> None:
        self.min_length = min_length
        self.hibp_check = hibp_check
        self.hibp_timeout = hibp_timeout

    async def validate(self, password: str) -> None:
        validate_password_strength(password, min_length=self.min_length)
        if not self.hibp_check:
            return
        try:
            count = await pwned_password_count(password, timeout=self.hibp_timeout)
        except Exception as exc:
            # Fail open on any lookup failure (network, HTTP, parse) — a HIBP
            # outage must not block legitimate password resets.
            logger.warning("HIBP breach check skipped (lookup failed): %s", exc)
            return
        if count > 0:
            raise ValueError(
                "This password has appeared in a known data breach. Please choose a different one."
            )
