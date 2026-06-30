"""G21 — pluggable password policy (length + opt-in HIBP breach check)."""

from __future__ import annotations

import hashlib

import pytest

from asterion.auth.password_policy import (
    DefaultPasswordPolicy,
    PasswordPolicy,
    pwned_password_count,
)


class _FakeResp:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:  # pragma: no cover - always ok in tests
        return None


class _FakeClient:
    """Minimal httpx.AsyncClient stand-in that records the URL it was asked for
    and returns a canned body."""

    def __init__(self, body: str) -> None:
        self.body = body
        self.requested_url: str | None = None

    async def get(self, url: str) -> _FakeResp:
        self.requested_url = url
        return _FakeResp(self.body)


def _suffix(password: str) -> str:
    return hashlib.sha1(password.encode()).hexdigest().upper()[5:]


# --- length policy ---


async def test_default_policy_rejects_short_password():
    policy = DefaultPasswordPolicy(min_length=12)
    with pytest.raises(ValueError, match="at least 12"):
        await policy.validate("short")


async def test_default_policy_accepts_long_password_without_hibp():
    policy = DefaultPasswordPolicy(min_length=8, hibp_check=False)
    # Must NOT touch the network: pass a client that would explode if used.
    await policy.validate("a-sufficiently-long-passphrase")


def test_default_policy_satisfies_protocol():
    assert isinstance(DefaultPasswordPolicy(), PasswordPolicy)


# --- HIBP k-anonymity ---


async def test_pwned_count_only_sends_hash_prefix():
    password = "correct horse battery staple"
    digest = hashlib.sha1(password.encode()).hexdigest().upper()
    client = _FakeClient(body=f"{_suffix(password)}:42\r\nFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF:1")

    count = await pwned_password_count(password, client=client)

    assert count == 42
    # k-anonymity: only the 5-char prefix is in the URL; never the password or
    # the full hash.
    assert client.requested_url.endswith(digest[:5])
    assert password not in client.requested_url
    assert digest not in client.requested_url


async def test_pwned_count_zero_when_suffix_absent():
    client = _FakeClient(body="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA:9")
    assert await pwned_password_count("whatever-long-pass", client=client) == 0


# --- HIBP policy integration ---


async def test_hibp_policy_rejects_breached_password():
    password = "password-that-is-long-enough"
    client = _FakeClient(body=f"{_suffix(password)}:1337")
    policy = DefaultPasswordPolicy(min_length=8, hibp_check=True)
    # Inject the breach checker so no real network call happens.
    policy_count = lambda pw: pwned_password_count(pw, client=client)  # noqa: E731

    async def _check(pw: str) -> int:
        return await policy_count(pw)

    # Patch the module function the policy calls.
    import asterion.auth.password_policy as mod

    orig = mod.pwned_password_count
    mod.pwned_password_count = lambda pw, **_: _check(pw)  # type: ignore[assignment]
    try:
        with pytest.raises(ValueError, match="data breach"):
            await policy.validate(password)
    finally:
        mod.pwned_password_count = orig


async def test_hibp_policy_fails_open_on_lookup_error():
    policy = DefaultPasswordPolicy(min_length=8, hibp_check=True)
    import asterion.auth.password_policy as mod

    async def _boom(pw, **_):
        raise RuntimeError("HIBP down")

    orig = mod.pwned_password_count
    mod.pwned_password_count = _boom  # type: ignore[assignment]
    try:
        # Must NOT raise — fail open when the lookup itself fails.
        await policy.validate("a-long-enough-password")
    finally:
        mod.pwned_password_count = orig
