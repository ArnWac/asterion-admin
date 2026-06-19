"""Record serializer.

B4 makes the read path aware of :class:`AdminPolicy.field_permission`:
when a caller's policy returns ``HIDDEN`` for a field, that field is
omitted from the serialized output, regardless of ``protected_fields``.
``READ`` permission still serializes normally (it only constrains
writes).

1.3 (Robustness): column-type coercion (UUID → str, datetime →
isoformat) is now sourced from the field adapter registry — the
serializer no longer carries its own hardcoded type switch. The fallback
``_value_fallback`` exists only for calculated fields, which have no
SQLAlchemy column and therefore no adapter to consult.

The policy hook is opt-in via the ``ctx=`` argument. Without ``ctx`` the
serializer keeps the pre-B4 behaviour exactly.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from asterion.fields import FieldRegistry, build_default_registry
from asterion.registry.admin import ModelAdmin

if TYPE_CHECKING:
    from asterion.admin.context import AdminContext


def _value_fallback(value: Any) -> Any:
    """Last-resort coercion for values that didn't come from a column
    (calculated fields). Mirrors what UUID/DateTime adapters do so a
    calculated field returning a UUID/datetime still hits the same
    wire shape."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _calculated_value(fn, obj) -> Any:
    try:
        return _value_fallback(fn(obj))
    except Exception:
        return None


async def _hidden_fields_via_policy(
    obj: object,
    model_admin: ModelAdmin,
    ctx: AdminContext | None,
) -> set[str]:
    """Compute the per-caller HIDDEN field set.

    Returns an empty set when no policy is attached or no ctx was
    supplied — preserves the pre-B4 wire format for legacy callers.
    """
    if ctx is None:
        return set()
    policy = getattr(model_admin, "policy", None)
    if policy is None:
        return set()
    from asterion.admin.policy import FieldPermission

    hidden: set[str] = set()
    for col in obj.__table__.columns:  # type: ignore[attr-defined]
        perm = await policy.field_permission(col.name, obj, ctx)
        if perm is FieldPermission.HIDDEN:
            hidden.add(col.name)
    for fname in model_admin.calculated_fields:
        perm = await policy.field_permission(fname, obj, ctx)
        if perm is FieldPermission.HIDDEN:
            hidden.add(fname)
    return hidden


class Serializer:
    """Column-aware record-to-dict serializer.

    Constructed once per app; holds a :class:`FieldRegistry` so each
    serialize call routes column values through their adapter's
    ``serialize(value, ctx)`` hook. Calculated fields (no column) fall
    back to ``_value_fallback`` for the same UUID/datetime coercion.

    Module-level instances exist for the legacy zero-config path
    (:func:`serialize_record` / :func:`serialize_records`). Both build
    the default registry lazily on first use.
    """

    def __init__(self, registry: FieldRegistry | None = None) -> None:
        self._registry = registry or build_default_registry()

    def serialize(
        self,
        obj: object,
        model_admin: ModelAdmin,
        *,
        hidden_extra: set[str] | None = None,
        ctx: AdminContext | None = None,
    ) -> dict:
        excluded = model_admin.all_protected
        if hidden_extra:
            excluded = excluded | frozenset(hidden_extra)
        result: dict = {}

        for col in obj.__table__.columns:  # type: ignore[attr-defined]
            if col.name in excluded:
                continue
            value = getattr(obj, col.name)
            adapter = self._registry.find_adapter(col)
            if adapter is not None:
                value = adapter.serialize(value, ctx)
            # Defensive fallback: a UUID/datetime value can land in a
            # column the adapter doesn't know to coerce (e.g. a generic
            # String column holding stringly-typed UUID PKs). The
            # fallback keeps the wire format JSON-safe regardless of
            # column declaration.
            value = _value_fallback(value)
            result[col.name] = value

        for fname, fn in model_admin.calculated_fields.items():
            if fname in excluded:
                continue
            result[fname] = _calculated_value(fn, obj)

        return result

    def serialize_many(
        self,
        objs: list,
        model_admin: ModelAdmin,
        *,
        hidden_extra: set[str] | None = None,
        ctx: AdminContext | None = None,
    ) -> list[dict]:
        return [
            self.serialize(obj, model_admin, hidden_extra=hidden_extra, ctx=ctx) for obj in objs
        ]


serializer = Serializer()


def serialize_record(
    obj,
    model_admin: ModelAdmin,
    schema=None,
    *,
    hidden_extra: set[str] | None = None,
) -> dict:
    return serializer.serialize(obj, model_admin, hidden_extra=hidden_extra)


def serialize_records(
    objs: list,
    model_admin: ModelAdmin,
    *,
    hidden_extra: set[str] | None = None,
) -> list[dict]:
    return serializer.serialize_many(objs, model_admin, hidden_extra=hidden_extra)


async def serialize_record_with_policy(
    obj,
    model_admin: ModelAdmin,
    ctx: AdminContext | None = None,
) -> dict:
    """Convenience entry-point used by the CRUD service path.

    Computes the policy-driven HIDDEN field set once and delegates to
    :func:`serialize_record`. Sync callers that don't have a ctx (tests,
    background jobs) can stay on :func:`serialize_record` directly.
    """
    hidden = await _hidden_fields_via_policy(obj, model_admin, ctx)
    return serialize_record(obj, model_admin, hidden_extra=hidden)


async def serialize_records_with_policy(
    objs: list,
    model_admin: ModelAdmin,
    ctx: AdminContext | None = None,
) -> list[dict]:
    """List-equivalent of :func:`serialize_record_with_policy`.

    Computes the HIDDEN set against the first row only when at least
    one row is present. Per-row HIDDEN decisions are deliberately not
    supported here — that would require an NxK policy call and break
    the wire format ("some rows have field X, others don't"). Apps
    that want per-row hiding should use ``can_view_object`` on those
    rows instead.
    """
    if not objs:
        return []
    hidden = await _hidden_fields_via_policy(objs[0], model_admin, ctx)
    return serialize_records(objs, model_admin, hidden_extra=hidden)
