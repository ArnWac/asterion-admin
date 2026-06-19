"""Central policy object: object- and resource-level access decisions.

A :class:`ModelAdmin` may set ``policy = MyPolicy()`` to layer
object-level / record-level rules on top of the existing
permission-key matcher. The framework runs both checks:

* permission key (``admin.<resource>.<action>``) — gates the route by
  the caller's grant set,
* policy method (``can_view_object`` / ``can_update_object`` /
  ``can_delete_object`` / ``can_view_model`` / ``can_create``,
  ``field_permission``) — gates the operation / individual field by
  app-defined rules (typically "the row's owner_id must equal the
  caller").

Both must allow for the operation to proceed. Defaults return permissive
values so an admin without a custom policy behaves exactly as before B3.

Policies are async because real-world checks often hit the DB ("does
this user share a team with the row's owner?"). Synchronous predicates
just ignore the ``async def`` ceremony — there's no cost.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from asterion.admin.context import AdminContext


# The ``str`` mix-in is intentional and load-bearing: members serialize as
# their value and compare equal to plain strings across the contract/UI.
# Switching to StrEnum (UP042) would change ``str(member)`` semantics.
class FieldPermission(str, Enum):  # noqa: UP042
    """How the current caller may interact with one field.

    * ``WRITE`` — full read + write access (the default).
    * ``READ`` — read access only; the field is omitted from the
      create/update schema and a payload that contains it is rejected
      by ``extra="forbid"``.
    * ``HIDDEN`` — the field is omitted everywhere — serialized
      output, contract, create/update schema. Treat as if the field
      didn't exist for this caller.

    Subclass values are ordered loosest → strictest. Inheriting from
    ``str`` keeps the enum JSON-serializable so the contract can ship
    the value directly.
    """

    WRITE = "write"
    READ = "read"
    HIDDEN = "hidden"

    @property
    def _rank(self) -> int:
        """Strictness rank: WRITE(0) < READ(1) < HIDDEN(2)."""
        return {"write": 0, "read": 1, "hidden": 2}[self.value]

    @classmethod
    def strictest(cls, *perms: FieldPermission) -> FieldPermission:
        """Combine several field-permission decisions, keeping the most
        restrictive (Roadmap 2.1).

        This is the single rule that unifies the three field-visibility
        mechanisms: a field's effective permission is the strictest of
        its static class (protected → HIDDEN, readonly → READ) and the
        per-caller :meth:`AdminPolicy.field_permission` decision. A
        policy can therefore only ever tighten access, never loosen
        what ``protected_fields`` / ``readonly_fields`` already locked
        down.

        Empty input defaults to WRITE (the permissive base).
        """
        result = cls.WRITE
        for perm in perms:
            if perm._rank > result._rank:
                result = perm
        return result


class AdminPolicy:
    """Default-allow policy. Subclass and override only the methods you
    want to constrain — every method has a permissive default.

    Method signatures intentionally mirror the planned future surface
    (``field_permission`` is per-field; ``record_filter`` for list
    scoping lands later). Adding new methods with safe defaults is
    backward-compatible.
    """

    async def can_view_model(self, ctx: AdminContext) -> bool:
        """Gate the entire admin (list + read + write). Use for
        resource-level visibility ("hide the Orders admin from
        non-staff users entirely")."""
        return True

    async def can_create(self, ctx: AdminContext) -> bool:
        """Per-resource create gate. Runs before payload validation —
        useful for "no new orders during freeze week" style rules."""
        return True

    async def can_view_object(self, obj: Any, ctx: AdminContext) -> bool:
        """Per-object read gate. Runs after the row has been fetched,
        before the response is built."""
        return True

    async def can_update_object(self, obj: Any, ctx: AdminContext) -> bool:
        """Per-object update gate. Runs after fetch, before
        ``validate_update`` / ``before_update``."""
        return True

    async def can_delete_object(self, obj: Any, ctx: AdminContext) -> bool:
        """Per-object delete gate. Runs after fetch, before
        ``before_delete``."""
        return True

    async def field_permission(
        self,
        field: str,
        obj: Any,
        ctx: AdminContext,
    ) -> FieldPermission:
        """Per-field decision for one caller.

        Returns :class:`FieldPermission`. Default is ``WRITE`` —
        every field is fully accessible. Override to hide or
        soft-readonly specific columns based on the caller's role or
        the object's state.

        ``obj`` is ``None`` on the create path (no row exists yet);
        policies that need the object should treat ``None`` as
        "first-time creation" and decide accordingly. The
        :class:`FieldPermission.HIDDEN` decision on create means
        "this field is invisible during creation" — the input is
        rejected and the field is missing from the form.
        """
        return FieldPermission.WRITE


class ReadOnlyPolicy(AdminPolicy):
    """Locks an admin to list + detail only (Roadmap 5.1).

    Used by built-in admins that should never be mutated through the
    framework — :class:`~asterion.builtins.admin.AuditLogAdmin` is
    the canonical caller. List + detail stay open; create / update /
    delete return ``False`` so the CRUD router answers 403 and the
    UI's contract-driven form rendering skips the action buttons.

    Field-level access is unchanged: read-only at the resource level
    does NOT imply HIDDEN/READ at the field level. If you also want
    to hide specific columns, layer a field-permission policy on top
    or set ``readonly_fields`` on the admin.
    """

    async def can_create(self, ctx: AdminContext) -> bool:
        return False

    async def can_update_object(self, obj: Any, ctx: AdminContext) -> bool:
        return False

    async def can_delete_object(self, obj: Any, ctx: AdminContext) -> bool:
        return False


# ---------------------------------------------------------------------------
# Field-visibility consolidation (Roadmap 2.1)
# ---------------------------------------------------------------------------

#: Columns the framework never lets a client write, regardless of admin
#: config — server-generated identity + timestamps. Mirrors
#: ``crud/payload.DEFAULT_READONLY_FIELD_NAMES`` but kept here so the
#: static resolver doesn't import the CRUD layer.
_AUTO_READONLY: frozenset[str] = frozenset(
    {"id", "created_at", "updated_at", "created_by", "updated_by", "deleted_at"}
)


def static_field_permission(model_admin: Any, field: str) -> FieldPermission:
    """Translate the *static* field-visibility config into a single
    :class:`FieldPermission` (Roadmap 2.1 / Audit A0.4).

    Resolution order (strictest first):

    1. ``field in model_admin.all_protected`` → ``HIDDEN`` — protected
       fields (global registry + per-admin ``protected_fields``) are
       invisible everywhere.
    2. ``field in model_admin.calculated_fields`` → ``READ`` —
       calculated fields have no underlying column to write back to.
    3. ``field in model_admin.readonly_fields`` or an auto-managed
       column (PK / timestamps) → ``READ`` — visible but not writable.
    4. otherwise → ``WRITE``.

    This is the one place that interprets ``protected_fields``,
    ``calculated_fields`` and ``readonly_fields`` as field permissions.
    The per-caller :meth:`AdminPolicy.field_permission` decision is
    layered on top via :meth:`FieldPermission.strictest` — a policy can
    tighten but never loosen what this static resolver returns.

    Synchronous on purpose: it reads only static admin attributes, so
    the contract / schema builders (also sync) can call it directly;
    the async policy hop stays separate.
    """
    protected = getattr(model_admin, "all_protected", frozenset())
    if field in protected:
        return FieldPermission.HIDDEN

    calculated = getattr(model_admin, "calculated_fields", {}) or {}
    if field in calculated:
        return FieldPermission.READ

    readonly = set(getattr(model_admin, "readonly_fields", []) or [])
    if field in readonly or field in _AUTO_READONLY:
        return FieldPermission.READ

    return FieldPermission.WRITE
