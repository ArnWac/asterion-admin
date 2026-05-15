"""Admin UI preferences endpoints."""
from fastapi import APIRouter, Depends

from adminfoundry.admin.ui_preferences import UIPreference, get_preferences, set_preferences
from adminfoundry.dependencies import get_current_user
from adminfoundry.models.user import User

router = APIRouter()


@router.get("/preferences")
async def get_user_preferences(
    current_user: User = Depends(get_current_user),
):
    """Return personal UI display preferences for the current user."""
    return get_preferences(str(current_user.id))


@router.put("/preferences")
async def update_user_preferences(
    prefs: UIPreference,
    current_user: User = Depends(get_current_user),
):
    """Persist personal UI display preferences — never overrides server permissions."""
    return set_preferences(str(current_user.id), prefs)
