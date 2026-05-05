"""
In-memory token blacklist keyed by JTI.
Expired entries are cleaned up on read; explicit clear_blacklist() for tests.
"""
from datetime import datetime, timezone

_blacklist: dict[str, float] = {}  # jti -> exp (unix timestamp)


def blacklist_token(jti: str, exp: float | int) -> None:
    _blacklist[jti] = float(exp)


def is_blacklisted(jti: str) -> bool:
    exp = _blacklist.get(jti)
    if exp is None:
        return False
    if datetime.now(timezone.utc).timestamp() >= exp:
        _blacklist.pop(jti, None)  # expired — clean up
        return False
    return True


def clear_blacklist() -> None:
    """Reset state — intended for test teardown only."""
    _blacklist.clear()
