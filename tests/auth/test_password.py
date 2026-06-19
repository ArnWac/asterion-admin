"""Tests for password hashing and verification."""

from __future__ import annotations

from asterion.auth.password import hash_password, verify_password


def test_hash_returns_non_empty_string():
    h = hash_password("my-password")
    assert isinstance(h, str)
    assert len(h) > 0


def test_hash_is_not_plaintext():
    h = hash_password("my-password")
    assert h != "my-password"


def test_verify_correct_password():
    h = hash_password("correct-horse")
    assert verify_password("correct-horse", h) is True


def test_verify_wrong_password():
    h = hash_password("correct-horse")
    assert verify_password("wrong-password", h) is False


def test_two_hashes_of_same_password_differ():
    h1 = hash_password("same")
    h2 = hash_password("same")
    assert h1 != h2


def test_empty_password_can_be_hashed():
    h = hash_password("")
    assert verify_password("", h) is True
