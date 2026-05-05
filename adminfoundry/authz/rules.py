from dataclasses import dataclass


@dataclass
class FieldPolicy:
    """Effective view/edit permission for a field given a user context."""
    can_view: bool = True
    can_edit: bool = True


@dataclass
class RecordPolicy:
    """Effective per-record access permissions for a user context."""
    can_read: bool = True
    can_update: bool = True
    can_delete: bool = True
