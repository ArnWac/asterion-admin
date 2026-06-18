"""Client-IP resolution behind proxies (Review R16)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from adminfoundry.core.net import client_ip


def _req(peer: str | None, xff: str | None = None):
    headers = {}
    if xff is not None:
        headers["x-forwarded-for"] = xff
    return SimpleNamespace(
        client=SimpleNamespace(host=peer) if peer is not None else None,
        headers=headers,
    )


def test_zero_trust_ignores_forwarded_for():
    # Default: never trust a client-supplied header — use the direct peer.
    req = _req("10.0.0.1", xff="1.2.3.4, 5.6.7.8")
    assert client_ip(req, trusted_proxy_count=0) == "10.0.0.1"


def test_one_proxy_takes_rightmost_forwarded_entry():
    # XFF = "<client>, <what the proxy saw>"; with 1 trusted proxy the real
    # client is the rightmost entry.
    req = _req("10.0.0.1", xff="203.0.113.9, 70.0.0.2")
    assert client_ip(req, trusted_proxy_count=1) == "70.0.0.2"


def test_two_proxies_takes_second_from_right():
    req = _req("10.0.0.1", xff="203.0.113.9, 70.0.0.2, 70.0.0.3")
    assert client_ip(req, trusted_proxy_count=2) == "70.0.0.2"


def test_count_exceeding_chain_falls_back_to_leftmost():
    req = _req("10.0.0.1", xff="203.0.113.9")
    assert client_ip(req, trusted_proxy_count=5) == "203.0.113.9"


def test_no_forwarded_header_falls_back_to_peer():
    req = _req("10.0.0.1")
    assert client_ip(req, trusted_proxy_count=1) == "10.0.0.1"


def test_empty_forwarded_header_falls_back_to_peer():
    req = _req("10.0.0.1", xff="   ")
    assert client_ip(req, trusted_proxy_count=1) == "10.0.0.1"


@pytest.mark.parametrize("count", [0, 1, 2])
def test_no_peer_no_header(count):
    req = _req(None)
    assert client_ip(req, trusted_proxy_count=count) is None
