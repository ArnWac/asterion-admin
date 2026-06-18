import bcrypt as _bcrypt


def hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return _bcrypt.checkpw(plain.encode(), hashed.encode())


# A fixed, precomputed bcrypt hash used to equalize login timing when the email
# is unknown (Review R15). Without this, the unknown-email branch skips bcrypt
# and returns faster than a wrong-password attempt, letting an attacker
# enumerate accounts by measuring response time. It is a hard-coded constant
# (not computed at import) so importing this hot module stays cheap; the
# plaintext is irrelevant and the value is not a secret. Cost factor 12 matches
# ``hash_password``'s ``gensalt()`` default, so the dummy verify costs the same
# as a real one.
_DUMMY_HASH = "$2b$12$5FxsSUmmDFkxxqlUG5y6s.sJaHStUx.mV7W2n4pF9dBl8Fmnht2T6"


def dummy_verify_password(plain: str) -> bool:
    """Run a throwaway bcrypt verify to match the cost of :func:`verify_password`.

    Always returns ``False``. Call it on the unknown-email branch of a login so
    the response time does not reveal whether the account exists.
    """
    return _bcrypt.checkpw(plain.encode(), _DUMMY_HASH.encode())


def validate_password_strength(password: str, *, min_length: int = 8) -> None:
    if len(password) < min_length:
        raise ValueError(f"Password must be at least {min_length} characters.")
