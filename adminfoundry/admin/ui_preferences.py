"""Personal UI preferences — non-security client-side display settings.

Backed by an in-memory store keyed by user_id string.
These preferences must never override server-enforced permissions,
field visibility rules, or protected-field filtering.
"""
from __future__ import annotations

from pydantic import BaseModel


class UIPreference(BaseModel):
    # visible_columns: per-model column selection override (display only)
    visible_columns: dict[str, list[str]] = {}
    # density: table/list display density hint
    density: str = "comfortable"  # "compact" | "comfortable" | "spacious"
    # sorting: per-model last-used sort column
    sorting: dict[str, str] = {}
    # navigation_favorites: model names pinned to the nav
    navigation_favorites: list[str] = []


# Keyed by str(user_id); cleared on process restart
_store: dict[str, dict] = {}


def get_preferences(user_id: str) -> UIPreference:
    return UIPreference.model_validate(_store.get(str(user_id), {}))


def set_preferences(user_id: str, prefs: UIPreference) -> UIPreference:
    _store[str(user_id)] = prefs.model_dump()
    return prefs


def clear_preferences() -> None:
    """Reset store — for tests only."""
    _store.clear()
